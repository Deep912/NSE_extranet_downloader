
# Import the pytest testing framework
import pytest

# Import the actual DownloaderService from our application
from app.services.downloader import DownloaderService


# TEST 1: Check whether a valid date is accepted
def test_valid_date():

    # Call the date validator and store its result
    result = DownloaderService._validate_date("04-11-2026")

    # Check whether the returned date matches our expectation
    assert result == "04-11-2026"


# TEST 2: Check whether an incorrect date format is rejected
def test_invalid_date_format():

    # We expect the validator to raise a ValueError
    with pytest.raises(ValueError):

        # Pass a date in YYYY-MM-DD format
        DownloaderService._validate_date("2026-11-04")


# TEST 3: Check whether an impossible calendar date is rejected
def test_impossible_date():

    # February cannot have 31 days
    with pytest.raises(ValueError):

        DownloaderService._validate_date("31-02-2026")


# TEST 4: Check whether an empty date is rejected
def test_empty_date():

    # An empty string is not a valid date
    with pytest.raises(ValueError):

        DownloaderService._validate_date("")


# TEST 5: A normal NSE folder path
def test_valid_folder_path():
    result = DownloaderService._validate_folder_path("/Reports")

    assert result == "Reports/"


# TEST 6: A folder path without a leading slash
def test_folder_path_without_leading_slash():
    result = DownloaderService._validate_folder_path("Reports")

    assert result == "Reports/"


# TEST 7: Reject an empty folder path
def test_empty_folder_path():
    with pytest.raises(ValueError):
        DownloaderService._validate_folder_path("")


# TEST 8: Reject Windows-style backslashes
def test_invalid_folder_path_separator():
    with pytest.raises(ValueError):
        DownloaderService._validate_folder_path("Reports\\Bulk")


# TEST 9: Reject directory traversal attempts
def test_folder_path_traversal():
    with pytest.raises(ValueError):
        DownloaderService._validate_folder_path("../Reports")

