"""Compare Ita? merchant names across open and closed statement exports."""
import re
import unicodedata


def merchant_name(value):
    text = str(value or "").strip()
    # Ita? uses 23 merchant characters for purchases and 18 for installments,
    # followed by a 14-character city and a two/three-letter state/country.
    if len(text) in {34, 35, 39, 40} and re.search(r"\s[A-Za-z]{2,3}$", text):
        width = 23 if len(text) in {39, 40} else 18
        text = text[:width].strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def equivalent_description(left, right):
    return merchant_name(left) == merchant_name(right)
