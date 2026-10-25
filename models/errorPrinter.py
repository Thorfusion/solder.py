class ErrorPrinter:
    @staticmethod
    def message(message, e=""):
        print(f"[solder.py] ERROR: {message}", flush=True)
        if e not in (None, ""):
            print(f"[solder.py] ERROR DETAIL: {e}", flush=True)
