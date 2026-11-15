"""Safe descriptions verified against Omada Controller 6.0.0.39.

Source: controller-served /locale/lan-1770199100768/en_US/err.js.
Only local, static descriptions are used; response messages may contain secrets.
Error code meanings may differ between controller versions.
"""

from typing import Final

OMADA_ERROR_DESCRIPTIONS: Final[dict[int, str]] = {
    -1: "Something went wrong. Please try again later or contact our technical support.",
    -1000: "Invalid json format.",
    -1001: "Invalid request parameters.",
    -1002: "Unsupported request method.",
    -1003: "Failed to load configuration.",
    -1004: "Invalid request.",
    -1005: "Operation forbidden.",
    -1006: "Invalid organization permission.",
    -1007: "The current user does not have permissions to access this page.",
    -1200: "You have been logged out of the controller. This may have been caused by web application switching, data restore, reboot, session timeout or disabled cloud access. Please try to log in again later.",
    -1202: "The current IP address does not have permission to access the system. Please contact the system administrator.",
    -1505: "The current user does not have permissions to access this site.",
    -20011: "Invalid username or password.",
    -30109: "Invalid username or password.",
    -30110: "Failed to log in to the controller because you have no permission of any site.",
    -30137: "Invalid username or password.",
    -33000: "This site does not exist.",
    -41001: "This client does not exist.",
    -41002: "This client does not exist.",
    -41003: "The number of clients to be blocked has reached the limit.",
    -41004: "This client does not exist.",
    -41005: "The authorization information does not exist.",
    -41006: "This client does not exist.",
    -41007: "This client does not exist.",
    -41008: "The number of clients to be configured with Rate Limit has reached the limit.",
    -41009: "Failed to authorize this client.",
    -41010: "This client does not exist.",
    -41011: "This client does not exist.",
    -41012: "Failed to assign a fixed IP address to this client because the gateway does not exist.",
    -41013: "This network does not exist.",
    -41014: "Select a network whose type is interface.",
    -41015: "Enter an IP address within the Network IP range.",
    -41016: "This IP address has been used by another client in the network. Please enter another one.",
    -41017: "Unauth failed because gateway not existed or disconnected.",
    -41018: "Unauth failed, try again when network recover.",
    -41019: "The client is being disconnected, please wait...",
    -41500: "Invalid authentication type.",
    -41501: "Failed to authenticate.",
    -41505: "The number of users has reached the limit.",
    -41506: "Invalid authorization information.",
    -41509: "Authorization already exists.",
    -41510: "Invalid username or password.",
    -41511: "Invalid username or password.",
    -41514: "MAC address is incorrect.",
    -41518: "This SSID does not exist.",
    -41524: "Authentication failed because of incorrect username or password.",
    -41525: "Authentication failed because of incorrect username or password.",
    -41526: "Authentication failed because the client is invalid.",
    -41529: "Incorrect username or password.",
    -41530: "Connecting to the RADIUS server times out.",
    -41535: "Authed client does not exist",
    -42005: "The user does not have permissions to access any site.",
}


def describe_api_error(code: int) -> str:
    """Keep the exact numeric code, including when its meaning is unknown."""
    description = OMADA_ERROR_DESCRIPTIONS.get(code, "Unknown controller error.")
    return f"{description} (error code {code})"
