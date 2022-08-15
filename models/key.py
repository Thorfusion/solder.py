from database import Database
import errorPrinter

class Key:
    @staticmethod
    def verify_key(key: str) -> bool:
        conn = Database.get_connection()
        cur = conn.cursor()
        sql = "SELECT * FROM `keys` WHERE api_key = %s"
        try:
            cur.execute(sql, (key,))
            return cur.fetchone()
        except Exception as e:
            errorPrinter.message("An error occurred whilst trying to fetch an API key", e)
        conn.close()
