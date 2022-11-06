class ErrorPrinter:
    @staticmethod
    def message(message, e=""):
        print("-" * 70 + "\n" + str(message) + "\n" + str(e) + "+\n" + "-" * 70)