
from app.services.nse_client import NSEExtranetClient


def main():
    segment = "CM"
    folder_path = "Reports/"
    exchange_date = "01-11-2026"

    print("=" * 60)
    print("NSE EXTRANET BULK DOWNLOAD TEST")
    print("=" * 60)

    try:
        with NSEExtranetClient() as client:

            # Authenticate with NSE LIVE API
            print("\n[1] Authenticating with NSE...")
            client._ensure_authenticated()
            print("[SUCCESS] Authentication completed.")

            # Prepare request parameters
            params = {
                "segment": segment,
                "folderPath": folder_path,
                "date": exchange_date,
            }

            headers = {
                "Authorization": f"Bearer {client._token}",
                "Accept": "*/*",
            }

            endpoint = (
                f"member/file/download/{client.version}"
            )

            # Build request without sending it
            request = client._http.build_request(
                "GET",
                endpoint,
                params=params,
                headers=headers,
            )

            print("\n[2] Request details")
            print("-" * 60)
            print("Method:", request.method)
            print("URL:", request.url)
            print("Segment:", segment)
            print("Folder:", folder_path)
            print("Date:", exchange_date)
            print("Authorization: Bearer [REDACTED]")

            # Send request
            print("\n[3] Sending request to NSE...")
            response = client._http.send(request)

            # Display response details
            print("\n[4] NSE response")
            print("-" * 60)
            print("HTTP Status:", response.status_code)
            print("Content-Type:", response.headers.get("content-type"))
            print("Content-Length:", response.headers.get("content-length"))
            print("Content-Disposition:", response.headers.get("content-disposition"))
            print("Response size:", len(response.content), "bytes")
            print("First 100 bytes:", response.content[:100])

            # Check response
            if response.status_code == 200:
                print("\n[SUCCESS] NSE accepted the bulk download request.")

                if response.content[:2] == b"\x1f\x8b":
                    print("[INFO] GZIP signature detected.")
                else:
                    print("[WARNING] Standard GZIP signature not detected.")

                # Save raw response for inspection
                output_file = "nse_bulk_test_response.bin"

                with open(output_file, "wb") as file:
                    file.write(response.content)

                print("[SAVED] Response saved to:", output_file)

            else:
                print("\n[FAILED] NSE rejected the request.")
                print("Response body:")
                print(response.text[:2000])

    except Exception as exc:
        print("\n[ERROR]", type(exc).__name__)
        print(str(exc))

    print("\n" + "=" * 60)
    print("TEST COMPLETED")
    print("=" * 60)


if __name__ == "__main__":
    main()
