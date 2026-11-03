class DownloaderService:
    """
    Coordinates segment downloads.

    NSE API calls are intentionally not implemented yet. The supplied FAQ
    does not contain the complete request/response specification or all
    listing/download semantics needed for a safe integration.
    """
    def start_segment_download(self, segment: str):
        raise NotImplementedError(
            "NSE integration is not configured yet. Add the complete "
            "Extranet API specification before enabling live downloads."
        )
