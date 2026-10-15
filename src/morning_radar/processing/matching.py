"""Small lexical alias boundary check, not entity or news-value inference."""
import re
import unicodedata


def alias_matches(alias: str, text: str) -> bool:
    alias = unicodedata.normalize("NFKC", alias).casefold().strip()
    text = unicodedata.normalize("NFKC", text).casefold()
    if not alias:
        return False
    if alias.isascii():
        # Adjacent version digits are allowed, embedded alphabetic names are not.
        return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z])", text) is not None
    return alias in text
