from core.youtube_auth import get_authenticated_service

youtube = get_authenticated_service()

request = youtube.channels().list(
    part="snippet",
    mine=True
)

response = request.execute()

for item in response["items"]:
    print("Authenticated Channel:", item["snippet"]["title"])