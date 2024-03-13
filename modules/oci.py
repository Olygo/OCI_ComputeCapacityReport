# coding: utf-8

import oci
import os
from modules.utils import green, yellow, print_error, print_info, check_file_size

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# check if bucket already exists
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def check_bucket(obj_storage_client, report_comp, report_bucket, tenancy_id):

    try:
        my_namespace = obj_storage_client.get_namespace(compartment_id=tenancy_id).data
        all_buckets = obj_storage_client.list_buckets(my_namespace,report_comp).data
        bucket_etag=''

        for bucket in all_buckets:
            if bucket.name == report_bucket:
                print_info(green, 'Bucket', 'found', bucket.name)
                bucket_etag=bucket.etag

        if len(bucket_etag) < 1:
            print_info(yellow, 'Bucket', 'creating', report_bucket)

            create_bucket_details = oci.object_storage.models.CreateBucketDetails(
                                                                                public_access_type = 'NoPublicAccess',
                                                                                storage_tier = 'Standard',
                                                                                versioning = 'Disabled',
                                                                                name = report_bucket,
                                                                                compartment_id = report_comp)
            
            # create bucket 
            obj_storage_client.create_bucket(my_namespace, create_bucket_details)                
            
            result_response = obj_storage_client.get_bucket(my_namespace, report_bucket)

            wait_until_bucket_available_response = oci.wait_until(
                                                                obj_storage_client,
                                                                result_response,
                                                                'etag',
                                                                result_response.data.etag)
            
            print_info(yellow, 'Bucket', 'created', wait_until_bucket_available_response.data.name)

    except oci.exceptions.ServiceError as response:
        print_error("Bucket error:", report_bucket, response.code, response.message)
        raise SystemExit(1)


# - - - - - - - - - - - - - - - - - - - - - - - - - -
# move csv report to oci
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def upload_file(obj_storage_client, report_bucket, csv_report, report_name, tenancy_id):

    try:
        if check_file_size(csv_report):
            namespace = obj_storage_client.get_namespace(compartment_id=tenancy_id).data

            # upload report to oci
            with open(csv_report, 'rb') as in_file:
                upload_response = obj_storage_client.put_object(
                                                                namespace,
                                                                report_bucket,
                                                                report_name,
                                                                in_file)

            # list objects in bucket and check md5 of uploaded file
            object_list = obj_storage_client.list_objects(
                                                        namespace,
                                                        report_bucket, 
                                                        fields=['md5'])

            for item in object_list.data.objects:
                if item.md5 == upload_response.headers['opc-content-md5']:
                    print(' '*20)
                    print(green(f"{'*'*94:94}"))
                    print_info(green, 'Upload', 'success', item.name)
                    print_info(green, 'MD5 checksum', 'success', item.md5)
                    print(green(f"{'*'*94:94}\n\n"))

                    # remove local report
                    os.remove(csv_report)
                    break
        else:
            print_error("Report does not contain any data",
                        csv_report,
                         "Upload process has been aborted",
                         level='INFO')

    except Exception as e:
        print_error("upload_file error", e)
        raise SystemExit(1)