"""Unit tests for core.url_parser."""

from core.url_parser import (
    category_for_url,
    decode_disposition_header,
    extract_urls,
    filename_with_extension,
    guess_extension,
    is_valid_url,
    url_category,
    url_filename,
)


def test_valid_urls():
    assert is_valid_url("https://example.com/file.zip")
    assert is_valid_url("http://example.com:8080/a/b/c.mp4")
    assert is_valid_url("ftp://ftp.example.com/files.iso")
    assert not is_valid_url("")
    assert not is_valid_url("not a url")
    assert not is_valid_url("javascript:alert(1)")
    assert not is_valid_url("https://")
    assert not is_valid_url("file:///etc/passwd")


def test_extract_urls_multiple():
    text = (
        "Download from https://a.com/x.zip or https://b.com/y.mp4!\n"
        "Also: http://c.com/z.txt\n"
        "duplicate https://a.com/x.zip"
    )
    urls = extract_urls(text)
    assert urls[0] == "https://a.com/x.zip"
    assert "https://b.com/y.mp4" in urls
    assert urls.count("https://a.com/x.zip") == 1
    assert len(urls) == 3


def test_url_filename():
    assert url_filename("https://x.com/files/My Report.pdf?sig=123") == "My Report.pdf"
    assert url_filename("https://x.com/dir/") == "dir"
    assert url_filename("https://x.com") == "x.com"
    # percent-encoded
    assert url_filename("https://x.com/get%20file.zip") == "get file.zip"


def test_decode_disposition():
    assert decode_disposition_header('attachment; filename="movie.mp4"') == "movie.mp4"
    assert decode_disposition_header("attachment; filename*=UTF-8''my%20file.mp4") == \
        "my file.mp4"
    assert decode_disposition_header("inline") is None
    assert decode_disposition_header("") is None


def test_guess_extension():
    assert guess_extension("video/mp4") == ".mp4"
    assert guess_extension("application/pdf; charset=binary") == ".pdf"
    assert guess_extension("") == ""
    assert guess_extension("application/octet-stream") == ""


def test_filename_with_extension():
    assert filename_with_extension("movie", "video/mp4") == "movie.mp4"
    assert filename_with_extension("movie.mp4", "video/mp4") == "movie.mp4"
    assert filename_with_extension("data", "application/unknown") == "data"


def test_category_from_url():
    assert url_category("https://x.com/a/b.pdf") == "documents"
    assert url_category("https://x.com/a/b.mp3") == "music"
    assert url_category("https://x.com/a/b.zip") == "compressed"
    assert url_category("https://x.com/a/b.exe") == "programs"
    assert category_for_url("https://x.com/a/stream") in (
        "others", "documents", "music", "videos", "images", "compressed", "programs")
