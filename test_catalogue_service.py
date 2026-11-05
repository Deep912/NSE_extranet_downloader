
from app.database import init_db
from app.services.folder_catalogue_service import FolderCatalogueService


init_db()

catalogue = FolderCatalogueService()

sample_scan = {
    "scan_complete": True,
    "failed_folders": [],
    "folders": [
        {
            "name": "Reports",
            "fullPath": "/Reports",
        },
        {
            "name": "Order_Reports",
            "fullPath": "/Reports/Order_Reports",
        },
    ],
    "files": [
        {
            "name": "report_A.csv",
            "folderPath": "/Reports",
            "lastUpdated": "2026-11-03T06:30:00+00:00",
        },
        {
            "name": "report_B.csv",
            "folderPath": "/Reports/Order_Reports",
            "lastUpdated": "2026-11-03T07:30:00+00:00",
        },
    ],
}

result = catalogue.save_scan_result(
    segment="CM",
    scan_result=sample_scan,
    service="member",
)

print("Scan result:", result)

print("\nCached folders:")
for folder in catalogue.get_folders("CM"):
    print(folder["folder_path"])

print("\nCached files:")
for file in catalogue.get_files("CM"):
    print(file["folder_path"], file["file_name"])
