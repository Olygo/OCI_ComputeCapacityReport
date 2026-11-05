class RestartFlowException(Exception):
    """Custom exception to restart from initial function when an error occurs."""
    pass

class HomeNavigation(BaseException):
    """Return to the session home without being caught as an API failure."""


def check_home(value):
    if value.upper() in {"H", "HOME"}:
        raise HomeNavigation


def home_prompt(prompt):
    if "[H]" in prompt:
        return prompt
    if "[B]ack" in prompt:
        return prompt.replace("[B]ack", "[H]ome, [B]ack", 1)
    if "[Q]uit" in prompt:
        return prompt.replace("[Q]uit", "[H]ome, [Q]uit", 1)
    return prompt.rstrip(" >: ") + " ([H]ome) > "
