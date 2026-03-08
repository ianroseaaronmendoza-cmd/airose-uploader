from core.youtube_auth import get_authenticated_service

if __name__ == "__main__":
    youtube = get_authenticated_service()
    print("Authentication successful.")