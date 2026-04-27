import unittest
from unittest.mock import Mock, patch

from core.pinterest_uploader import resolve_pinterest_link, upload_pinterest_pin_with_details


class PinterestUploaderTests(unittest.TestCase):
    def test_resolve_pinterest_link_does_not_fallback_to_media_urls(self) -> None:
        data = {
            "pinterest_link": "",
            "youtube_video_url": "https://www.youtube.com/watch?v=abc123",
            "public_video_url": "https://cdn.example.com/video.mp4",
            "instagram_video_url": "https://www.instagram.com/reel/xyz/",
        }

        self.assertEqual(resolve_pinterest_link(data), "")

    def test_resolve_pinterest_link_accepts_explicit_website_field(self) -> None:
        data = {
            "website_url": "https://example.com/article",
            "youtube_video_url": "https://www.youtube.com/watch?v=abc123",
        }

        self.assertEqual(resolve_pinterest_link(data), "https://example.com/article")

    @patch("core.pinterest_uploader.requests.post")
    @patch("core.pinterest_uploader._build_media_source")
    @patch("core.pinterest_uploader.get_pinterest_config")
    def test_upload_retries_without_link_on_site_block(
        self,
        mock_get_config: Mock,
        mock_build_media_source: Mock,
        mock_post: Mock,
    ) -> None:
        mock_get_config.return_value = {
            "access_token": "token",
            "board_id": "board-1",
            "board_section_id": "",
            "api_base_url": "https://api.pinterest.com",
            "media_source_type": "video_url",
            "cover_image_key_frame_time": 0,
        }
        mock_build_media_source.return_value = {
            "source_type": "video_id",
            "media_id": "media-1",
            "cover_image_key_frame_time": 0,
        }

        blocked_response = Mock()
        blocked_response.ok = False
        blocked_response.status_code = 400
        blocked_response.json.return_value = {
            "code": 1,
            "message": "Sorry! This site doesn't allow you to save Pins.",
        }
        blocked_response.headers = {}

        success_response = Mock()
        success_response.ok = True
        success_response.status_code = 201
        success_response.json.return_value = {"id": "pin-1"}
        success_response.headers = {}

        mock_post.side_effect = [blocked_response, success_response]

        pin_id, payload_used, _ = upload_pinterest_pin_with_details(
            title="Title",
            description="Description",
            link="https://example.com/article",
            board_id="board-1",
        )

        self.assertEqual(pin_id, "pin-1")
        self.assertNotIn("link", payload_used)
        self.assertEqual(mock_post.call_count, 2)

        first_payload = mock_post.call_args_list[0].kwargs["json"]
        second_payload = mock_post.call_args_list[1].kwargs["json"]
        self.assertEqual(first_payload["link"], "https://example.com/article")
        self.assertNotIn("link", second_payload)

    @patch("core.pinterest_uploader.refresh_saved_pinterest_token_non_interactive")
    @patch("core.pinterest_uploader.requests.post")
    @patch("core.pinterest_uploader._build_media_source")
    @patch("core.pinterest_uploader.get_pinterest_config")
    def test_upload_refreshes_token_once_on_auth_failure(
        self,
        mock_get_config: Mock,
        mock_build_media_source: Mock,
        mock_post: Mock,
        mock_refresh_token: Mock,
    ) -> None:
        mock_get_config.side_effect = [
            {
                "access_token": "old-token",
                "board_id": "board-1",
                "board_section_id": "",
                "api_base_url": "https://api.pinterest.com",
                "media_source_type": "video_url",
                "cover_image_key_frame_time": 0,
            },
            {
                "access_token": "new-token",
                "board_id": "board-1",
                "board_section_id": "",
                "api_base_url": "https://api.pinterest.com",
                "media_source_type": "video_url",
                "cover_image_key_frame_time": 0,
            },
        ]
        mock_build_media_source.return_value = {
            "source_type": "video_id",
            "media_id": "media-1",
            "cover_image_key_frame_time": 0,
        }

        unauthorized_response = Mock()
        unauthorized_response.ok = False
        unauthorized_response.status_code = 401
        unauthorized_response.json.return_value = {
            "code": 2,
            "message": "Unauthorized",
        }
        unauthorized_response.headers = {}

        success_response = Mock()
        success_response.ok = True
        success_response.status_code = 201
        success_response.json.return_value = {"id": "pin-2"}
        success_response.headers = {}

        mock_post.side_effect = [unauthorized_response, success_response]

        pin_id, _, _ = upload_pinterest_pin_with_details(
            title="Title",
            description="Description",
            board_id="board-1",
        )

        self.assertEqual(pin_id, "pin-2")
        mock_refresh_token.assert_called_once_with()
        self.assertEqual(mock_post.call_count, 2)

        first_headers = mock_post.call_args_list[0].kwargs["headers"]
        second_headers = mock_post.call_args_list[1].kwargs["headers"]
        self.assertEqual(first_headers["Authorization"], "Bearer old-token")
        self.assertEqual(second_headers["Authorization"], "Bearer new-token")


if __name__ == "__main__":
    unittest.main()
