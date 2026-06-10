"""
Auth0 Authentication - Skippable for local development.
"""
import streamlit as st
from config import AUTH0_ENABLED, AUTH0_CONFIG, REQUIRED_ROLE


def check_auth():
    """
    Gate the app behind Auth0 login.

    When AUTH0_ENABLED=False (local dev): returns immediately with dev credentials.
    When AUTH0_ENABLED=True (deployed):  shows Auth0 login, validates JWT role.

    Returns:
        (authenticated: bool, user_name: str, roles: list)
    """
    if not AUTH0_ENABLED:
        return True, "Dev User", [REQUIRED_ROLE]

    # --- Auth0 login flow ---
    try:
        from auth0_component import login_button
        import jwt
    except ImportError:
        st.error(
            "Auth0 dependencies not installed. "
            "Run: pip install streamlit-auth0-component-patch PyJWT"
        )
        st.stop()

    result = login_button(
        AUTH0_CONFIG["clientId"],
        AUTH0_CONFIG["domain"],
    )

    if not result:
        st.info("Please log in to access Hermes.")
        st.stop()

    token = result.get("token")
    if not token:
        st.warning("No token received. Please try again.")
        st.stop()

    try:
        decoded = jwt.decode(token, options={"verify_signature": False})
        user_name = result.get("name", "User")
        roles = (
            decoded.get("https://wfscorp.com/custom-claims", {}).get("roles", [])
            or decoded.get("roles", [])
        )

        if REQUIRED_ROLE not in roles:
            st.error("You do not have the required role to access this app.")
            st.stop()

        return True, user_name, roles

    except Exception as e:
        st.error(f"Could not verify your role. Error: {e}")
        st.stop()
