"""直播语音：URL 必须免 token（<audio> / OBS 带不了 Authorization 头）。"""
import api_server
from live import voice


def test_audio_url_is_token_free():
    url = voice.audio_url("0" * 40 + ".mp3")
    assert url == "/live/audio/" + "0" * 40 + ".mp3"
    assert not url.startswith("/v1/")


def test_audio_name_validation_accepts_hash_files():
    assert api_server._valid_live_audio("0123456789abcdef0123456789abcdef01234567.mp3")
    assert api_server._valid_live_audio("0" * 40 + ".wav")


def test_audio_name_validation_rejects_traversal_and_junk():
    assert not api_server._valid_live_audio("../secret.mp3")
    assert not api_server._valid_live_audio("abc.mp3")          # 太短
    assert not api_server._valid_live_audio("0" * 41 + ".mp3")  # 太长
    assert not api_server._valid_live_audio("0" * 40 + ".exe")
    assert not api_server._valid_live_audio("Z" * 40 + ".mp3")  # 只认小写 hex
    assert not api_server._valid_live_audio("")
