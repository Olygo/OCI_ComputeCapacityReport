# coding: utf-8

# - - - - - - - - - - - - - - - - - - - - - - - - - - - -
# name: OCI_ComputeCapacityReport.py
#
# Author: Florian Bonneville
# Version: 4.0.0 - November 05, 2026
#
# Disclaimer: 
# This script is an independent tool developed by 
# Florian Bonneville and is not affiliated with or 
# supported by Oracle. It is provided as-is and without 
# any warranty or official endorsement from Oracle
#
# - - - - - - - - - - - - - - - - - - - - - - - - - - - -
version = '4.0.0'
author = ('Florian', 'Bonneville')

import oci
import argparse
import os.path
from modules.terminal import configure_terminal, status_message
from modules.utils import green, red, clear, print_info
from modules.exceptions import RestartFlowException, HomeNavigation
from modules.identity import (
    init_authentication, get_region_subscription_list, validate_region_connectivity,
    get_home_region, set_user_compartment, select_region,
)
from modules.capacity import (
    denseio_flex_shapes, process_regions, set_denseio_shape_ocpus,
    set_user_shape_name, set_user_shape_ocpus, set_user_shape_memory, print_shape_list,
    resolve_shape_regions, positive_integer,
    print_capacity_notice,
)
from modules.session import ReportSession, SearchRequest, HistoryEntry
from modules.execution import client_options, cancellation_scope, cancellable_call, build_retry_strategy
from modules.results import print_request_error, ReportSnapshot, ShapeResources


EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_PARTIAL = 3
EXIT_EXPORT_FAILURE = 4
EXIT_INTERRUPTED = 130


def search_exit_code(snapshot, *, failed=False, interrupted=False):
    """Describe query completion, independently of OCI capacity statuses."""
    if interrupted or (snapshot is not None and snapshot.interrupted):
        return EXIT_INTERRUPTED
    if snapshot is None or snapshot.finished_at is None or not snapshot.results:
        return EXIT_FAILURE
    errors = sum(result.error is not None for result in snapshot.results)
    if errors == len(snapshot.results):
        return EXIT_FAILURE
    if failed or errors or snapshot.is_partial or snapshot.outcome == 'ERROR':
        return EXIT_PARTIAL
    return EXIT_SUCCESS


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-color', action='store_true', help='Disable terminal colors and bold styling')
    parser.add_argument('--help-full', action='store_true', help='Open the complete paginated user guide before authentication')

    parser.add_argument('-auth', default='', choices=('cs', 'cf', 'ip'), dest='user_auth',
                         help='Force an authentication method : cs (cloudshell), cf (config file), ip (instance principals')
    
    parser.add_argument('-config_file', default='~/.oci/config', dest='config_file_path',
                        help='Path to your OCI config file, default: ~/.oci/config')
    
    parser.add_argument('-profile', default='DEFAULT', dest='config_profile',
                        help='Config file section to use, default: DEFAULT')

    parser.add_argument('-su', action='store_true',default=False, dest='su',
                        help='Skip the tenancy-admin question; an explicit -comp takes precedence')

    parser.add_argument('-comp', default='', dest='compartment', metavar='NAME_OR_OCID',
                        help='Compartment OCID or exact case-sensitive name; quote names containing spaces')
    
    parser.add_argument('-region', default='', dest='target_region',
                        help='Region name(s), comma-separated, or "all_regions"; default is home region')

    parser.add_argument('-skip_discovery', '--skip-discovery', action='store_true',
                        help='Skip automatic shape discovery in all_regions mode; use U to discover manually')
 
    parser.add_argument('-shape', default='', dest='shape',
                        help='Search this shape once, then exit instead of opening the interactive menu')
    parser.add_argument('-output', '--output', help='With -shape, export the report to a new .json or .csv file')
    
    parser.add_argument('-ocpus', type=positive_integer, dest='ocpus',
                        help='Indicate a specific ocpus amount')
    
    parser.add_argument('-memory', type=positive_integer, dest='memory',
                        help='Indicate a specific memory amount')

    parser.add_argument('-drcc', action='store_true',default=False, dest='drcc',
                        help='Print "available_count" value for DRCC customers and whitelisted tenancies')

    parser.add_argument('-debug', '--debug', action='store_true',
                        help='Show full request error diagnostics, including SDK version, sent configuration and request ID')

    args = parser.parse_args(argv)
    if args.help_full:
        return args
    if args.output is not None and (not args.shape or os.path.splitext(args.output)[1].lower() not in {'.csv', '.json'}):
        parser.error('-output requires -shape and a .csv or .json output path')
    if args.skip_discovery and args.target_region.lower() != 'all_regions':
        parser.error('-skip_discovery requires -region all_regions')
    if args.shape in denseio_flex_shapes and args.ocpus is not None:
        if str(args.ocpus) not in denseio_flex_shapes[args.shape]:
            parser.error(f'-ocpus for {args.shape} must be one of {denseio_flex_shapes[args.shape]}')
    return args


def run_query(session, args, user_shape_name):
    """Run a fresh capacity query using metadata cached in the session."""
    if user_shape_name and session.capacity_notice_pending:
        print_capacity_notice()
        session.capacity_notice_pending = False
    if not user_shape_name:
        def change_region():
            selected_region = select_region(
                session.identity_client(session.current_region),
                session.config['tenancy'], session.current_region,
                session.config, session.signer, debug=getattr(session, 'debug', False),
                single_only=session.all_regions
            )
            if selected_region is not None:
                if session.all_regions:
                    session.use_single_region(selected_region)
                else:
                    session.select_regions(selected_region if isinstance(selected_region, list) else [selected_region])
                clear()
                print_capacity_notice()
                session.capacity_notice_pending = False
                print_shape_list(session)
            return selected_region

        def switch_all_regions():
            try:
                subscriptions = cancellable_call(
                    session.identity_client(session.current_region).list_region_subscriptions,
                    session.config['tenancy'], retry_strategy=build_retry_strategy()
                ).data
                if not subscriptions:
                    print(red('No subscribed regions returned. Keeping the current selection.'))
                    return
                validated = validate_region_connectivity(
                    subscriptions, session.config, session.signer, fatal=False)
                if not validated:
                    print(red('No accessible region found. Keeping the current selection.'))
                    return
            except Exception as error:
                print_request_error('Unable to switch to all_regions; keeping the current selection',
                                    error, debug=session.debug)
                return
            session.use_all_regions(validated, len(subscriptions))
            clear()
            print_capacity_notice()
            session.capacity_notice_pending = False
            print_shape_list(session)

        if not session.catalog_shown:
            print_shape_list(session)
        user_shape_name = set_user_shape_name(
            session, change_region=change_region, switch_all_regions=switch_all_regions
        )

    replay = user_shape_name if isinstance(user_shape_name, SearchRequest) else None
    query_scope = replay.regions if replay is not None else tuple(session.regions)
    compartment_id = replay.compartment_id if replay is not None else session.compartment_id
    all_regions = replay.all_regions if replay is not None else session.all_regions
    drcc = replay.drcc if replay is not None else args.drcc
    user_shape_name = replay.shape if replay is not None else user_shape_name
    if replay is not None:
        print(green('Replaying a search in its original context.'))
    query_regions = resolve_shape_regions(
        session, user_shape_name, regions=query_scope,
        compartment_id=compartment_id, all_regions=all_regions
    )

    if replay is not None:
        user_shape_ocpus, user_shape_memory = replay.ocpus, replay.memory
    elif user_shape_name in denseio_flex_shapes:
        if args.ocpus is not None and str(args.ocpus) in denseio_flex_shapes[user_shape_name]:
            user_shape_ocpus = float(args.ocpus)
        else:
            if args.ocpus is not None:
                print(f'Invalid OCPU choice for {user_shape_name}: {args.ocpus}. Select a documented preset.')
            user_shape_ocpus = float(set_denseio_shape_ocpus(user_shape_name))
        user_shape_memory = args.memory
        print()
    elif ".Flex" not in user_shape_name or user_shape_name.startswith('BM.'):
        user_shape_ocpus = 0
        user_shape_memory = 0
    else:
        user_shape_ocpus = args.ocpus if args.ocpus is not None else set_user_shape_ocpus(user_shape_name)
        user_shape_memory = args.memory if args.memory is not None else set_user_shape_memory(user_shape_name)

    request = SearchRequest(user_shape_name, user_shape_ocpus, user_shape_memory,
                            tuple(query_scope), compartment_id, all_regions, drcc)
    entry = HistoryEntry(request)
    session.history.append(entry)
    snapshot = ReportSnapshot(
        user_shape_name, tuple(region.region_name for region in query_scope),
        tuple(region.region_name for region in query_regions), compartment_id,
        ShapeResources(user_shape_ocpus, user_shape_memory), all_regions, drcc,
        started_at=entry.started_at
    )
    session.last_report = snapshot
    try:
        results = process_regions(
            session, query_regions, user_shape_name, user_shape_ocpus,
            user_shape_memory, drcc, compartment_id=compartment_id, snapshot=snapshot
        )
        entry.outcome = 'ERROR' if any(result.error is not None for result in results) else 'COMPLETED'
        return results
    except KeyboardInterrupt:
        entry.outcome = 'INTERRUPTED'
        if snapshot.finished_at is None:
            snapshot.finish(snapshot.results, snapshot.completed_regions, snapshot.requests_count,
                            snapshot.elapsed_seconds, interrupted=True)
        snapshot.interrupted = snapshot.is_partial = True
        snapshot.outcome = 'INTERRUPTED'
        raise
    except BaseException:
        entry.outcome = 'ERROR'
        if snapshot.finished_at is None:
            snapshot.finish(snapshot.results, snapshot.completed_regions, snapshot.requests_count,
                            snapshot.elapsed_seconds)
        snapshot.outcome = 'ERROR'
        snapshot.is_partial = True
        raise


def main(argv=None):
    """Run one search with -shape, otherwise start an interactive session."""
    args = parse_arguments(argv)
    configure_terminal(args.no_color)
    if args.help_full:
        from modules.help_guide import show_help
        try:
            show_help()
        except HomeNavigation:
            # No authenticated session exists to return to in this mode.
            pass
        return EXIT_SUCCESS
    clear()
    config, signer, tenancy, auth_name, details = init_authentication(
        args.user_auth, args.config_file_path, args.config_profile
    )
    clear()
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    print(green(f"\n{'*'*94:94}"))
    print_info(green, 'Script', 'started', script_name)
    print_info(green, 'Script', 'version', version)
    print_info(green, 'Author', author[0], author[1])
    print_info(green, 'Login', 'success', auth_name)
    print_info(green, 'Login', 'profile', details)
    print_info(green, 'Tenancy', tenancy.name, f'home region: {tenancy.home_region_key}')

    from modules.reference_catalog import load_reference_catalog
    reference_catalog = load_reference_catalog(debug=args.debug)

    identity_client = oci.identity.IdentityClient(config=config, signer=signer, **client_options())
    tenancy_id = config['tenancy']
    regions_to_analyze = get_region_subscription_list(identity_client, tenancy_id, args.target_region)
    regions_validated = validate_region_connectivity(regions_to_analyze, config, signer)
    home_region = get_home_region(identity_client, tenancy_id)

    if args.shape:
        print_info(green, 'Shape', 'analyzed', args.shape)
    if args.ocpus:
        print_info(green, 'oCPUs', 'amount', f'{args.ocpus} cores')
    if args.memory:
        print_info(green, 'Memory', 'amount', f'{args.memory} gbs')
    print(green(f"{'*'*94:94}\n"))
    print_capacity_notice()

    user_compartment = set_user_compartment(identity_client, args, tenancy_id)
    session = ReportSession(
        config, signer, regions_validated, home_region, user_compartment,
        all_regions=args.target_region.lower() == 'all_regions',
        skip_discovery=args.skip_discovery, debug=args.debug,
        subscribed_region_count=len(regions_to_analyze), reference_catalog=reference_catalog
    )
    session.capacity_notice_pending = False
    user_shape_name = args.shape
    while True:
        failed = interrupted = False
        try:
            with cancellation_scope():
                run_query(session, args, user_shape_name)
        except HomeNavigation:
            if args.shape:
                return EXIT_INTERRUPTED
            clear()
            session.catalog_shown = False
            user_shape_name = ""
            continue
        except RestartFlowException:
            failed = True
        except KeyboardInterrupt:
            interrupted = True
            status_message('Search interrupted.' + ('' if args.shape else ' Returning to the menu.'))
        except EOFError:
            print('\nEnd of input. Quitting.')
            return EXIT_FAILURE if args.shape else EXIT_SUCCESS
        except Exception as e:
            failed = True
            context = 'Search failed' if args.shape else 'Search failed; returning to the menu'
            print_request_error(context, e, debug=args.debug)
        if args.shape:
            status = search_exit_code(session.last_report, failed=failed, interrupted=interrupted)
            if args.output and session.last_report is None:
                print(red('No capacity report was created; nothing to export.'))
            elif args.output:
                from modules.exports import export_report
                try:
                    target = export_report(session.last_report, args.output,
                                           os.path.splitext(args.output)[1][1:].lower())
                    print(green(f'Report exported to {target}'))
                except (OSError, ValueError, RuntimeError) as error:
                    print(red(f'Report export failed: {error}'))
                    return EXIT_INTERRUPTED if status == EXIT_INTERRUPTED else EXIT_EXPORT_FAILURE
                except KeyboardInterrupt:
                    status_message('Report export interrupted.')
                    return EXIT_INTERRUPTED
                except Exception as error:
                    print_request_error('Report export failed', error, debug=args.debug)
                    return EXIT_INTERRUPTED if status == EXIT_INTERRUPTED else EXIT_EXPORT_FAILURE
            return status
        user_shape_name = ''


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        status_message('Startup interrupted.')
        raise SystemExit(EXIT_INTERRUPTED)
    except EOFError:
        print('\nEnd of input. Quitting.')
        raise SystemExit(EXIT_FAILURE)
