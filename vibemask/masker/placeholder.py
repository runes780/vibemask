"""
Placeholder generator for masking sensitive data.
Generates unique, natural-looking replacements.
"""

from typing import Dict, Optional
from ..core.span import EntityType


# ============================================================
# Chinese Name Generation
# ============================================================

# Surnames for fake names (避开常用真实姓氏)
FAKE_SURNAMES = ["赵", "钱", "孙", "李", "周", "吴", "郑", "王"]

# Name characters (天干地支 - looks natural, unique combinations)
NAME_CHARS_TIANGAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
NAME_CHARS_DIZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


class PlaceholderGenerator:
    """
    Generates unique, stable placeholders for sensitive entities.
    
    Design principles:
    1. Same original -> same placeholder (stable within project)
    2. Placeholder length matches original (format preservation)
    3. Natural-looking to reduce AI modification
    """
    
    def __init__(self):
        self._counters: Dict[EntityType, int] = {t: 0 for t in EntityType}
        self._cache: Dict[tuple, str] = {}  # (original, type) -> masked
    
    def generate(self, original: str, entity_type: EntityType) -> str:
        """
        Generate a placeholder for the given entity.
        
        Args:
            original: Original sensitive text
            entity_type: Type of entity
            
        Returns:
            Masked placeholder text
        """
        cache_key = (original, entity_type)
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        if entity_type == EntityType.PERSON:
            masked = self._generate_person_name(original)
        elif entity_type == EntityType.PHONE:
            masked = self._generate_phone(original)
        elif entity_type == EntityType.EMAIL:
            masked = self._generate_email(original)
        elif entity_type == EntityType.IDCN:
            masked = self._generate_idcn(original)
        elif entity_type == EntityType.ORG:
            masked = self._generate_org(original)
        elif entity_type == EntityType.ADDRESS:
            masked = self._generate_address(original)
        elif entity_type == EntityType.URL:
            masked = self._generate_url(original)
        elif entity_type == EntityType.DATE_TIME:
            masked = self._generate_date_time(original)
        else:
            masked = self._generate_generic(original, entity_type)
        
        self._cache[cache_key] = masked
        return masked
    
    def _generate_url(self, original: str) -> str:
        """Generate length-preserving masked URL."""
        import re
        counter = self._counters[EntityType.URL]
        self._counters[EntityType.URL] += 1
        
        length = len(original)
        
        # Try to preserve domain but mask the path with length-preserving placeholder
        match = re.match(r'(https?://[^/]+)(.*)', original)
        if match:
            domain = match.group(1)
            path = match.group(2)
            path_len = len(path)
            
            if path_len > 0:
                # Create a length-preserving path replacement
                # Format: /U{counter} padded with 'X' to match length
                base_path = f"/U{counter:03d}"
                if len(base_path) < path_len:
                    base_path += "X" * (path_len - len(base_path))
                elif len(base_path) > path_len:
                    base_path = base_path[:path_len]
                return domain + base_path
            else:
                return domain
        else:
            # Fallback: create length-preserving generic URL
            base = f"https://example.com/U{counter:03d}"
            if len(base) < length:
                base += "X" * (length - len(base))
            return base[:length]
    
    def _generate_person_name(self, original: str) -> str:
        """Generate fake Chinese name with same length.
        
        Algorithm ensures unique combinations for large numbers:
        - Use counter to generate unique (surname_idx, name_chars) tuple
        - For 2-char names: 8 surnames × 10 tiangan × 12 dizhi = 960 combinations
        - For 3-char names: 8 × 10 × 12 = 960 combinations  
        - For 4-char names: 8 × 10 × 12 × 10 = 9600 combinations
        """
        length = len(original)
        counter = self._counters[EntityType.PERSON]
        self._counters[EntityType.PERSON] += 1
        
        num_surnames = len(FAKE_SURNAMES)  # 8
        num_tiangan = len(NAME_CHARS_TIANGAN)  # 10
        num_dizhi = len(NAME_CHARS_DIZHI)  # 12
        
        # Use a unique decomposition: treat counter as a multi-base number
        # This ensures no repeats until we exhaust all combinations
        remaining = counter
        
        # First decompose for name characters (we need name_length chars)
        name_length = length - 1
        if name_length <= 0:
            name_length = 1
        
        name_chars = []
        for i in range(name_length):
            if i == 0:
                # First name char: use tiangan
                char_list = NAME_CHARS_TIANGAN
            else:
                # Second name char: use dizhi
                char_list = NAME_CHARS_DIZHI
            char_idx = remaining % len(char_list)
            name_chars.append(char_list[char_idx])
            remaining //= len(char_list)
        
        # After exhausting name chars, use remaining for surname
        surname_idx = remaining % num_surnames
        surname = FAKE_SURNAMES[surname_idx]
        
        result = surname + "".join(name_chars)
        
        # Ensure same length (should already be correct)
        if len(result) < length:
            extra_idx = (counter // (num_tiangan * num_dizhi)) % num_dizhi
            result += NAME_CHARS_DIZHI[extra_idx]
        result = result[:length]
        
        return result
    
    def _generate_phone(self, original: str) -> str:
        """Generate masked phone number (length-preserving, separator-preserving, reversible)."""
        digits_only = "".join(c for c in original if c.isdigit())
        if not digits_only:
            return original

        counter = self._counters[EntityType.PHONE]
        self._counters[EntityType.PHONE] += 1

        # Generate a pseudo phone number with the same digit count.
        digit_count = len(digits_only)
        if digit_count == 11:
            # Make it look like a CN mobile number (1[3-9]xxxxxxxxx) without leaking the original.
            second = str(3 + (counter % 7))  # 3-9
            rest = f"{(counter // 7) % 1_000_000_000:09d}"
            masked_digits = "1" + second + rest
        else:
            # Fallback for other phone-like strings (landlines, extensions, etc.).
            masked_digits = f"{counter:0{digit_count}d}"[-digit_count:]

        # Preserve separators by replacing digits in-place.
        out = []
        i = 0
        for ch in original:
            if ch.isdigit():
                out.append(masked_digits[i])
                i += 1
            else:
                out.append(ch)
        return "".join(out)
    
    def _generate_email(self, original: str) -> str:
        """Generate masked email (keep domain structure)."""
        if '@' not in original:
            return original
        
        local, domain = original.rsplit('@', 1)
        counter = self._counters[EntityType.EMAIL]
        self._counters[EntityType.EMAIL] += 1
        
        # Generate unique local part with similar length
        new_local = f"u{counter:03d}"
        
        # Pad to match original length if needed
        while len(new_local) < len(local):
            new_local += "x"
        new_local = new_local[:len(local)]
        
        return f"{new_local}@{domain}"
    
    def _generate_idcn(self, original: str) -> str:
        """Generate pseudo Chinese ID card number (length-preserving, reversible)."""
        counter = self._counters[EntityType.IDCN]
        self._counters[EntityType.IDCN] += 1

        def calc_check_digit(base17: str) -> str:
            weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
            check_chars = "10X98765432"
            total = sum(int(base17[i]) * weights[i] for i in range(17))
            return check_chars[total % 11]

        if len(original) == 18 and original[:-1].isdigit():
            # Use a fixed (non-real) region code and synthesize date/sequence.
            region = "110101"
            year = 1980 + ((counter // 366) % 30)
            day_of_year = (counter % 366) + 1
            month = (day_of_year - 1) // 31 + 1
            day = (day_of_year - 1) % 31 + 1
            seq = counter % 1000
            base17 = f"{region}{year:04d}{month:02d}{day:02d}{seq:03d}"
            check = calc_check_digit(base17)
            return base17 + check

        # Fallback: keep length and character class roughly similar.
        if original.isdigit():
            return f"{counter:0{len(original)}d}"[-len(original):]
        # Preserve last char case if X/x.
        if len(original) == 18 and original[-1] in {"X", "x"}:
            base = f"{counter:017d}"[-17:]
            check = calc_check_digit(base)
            return base + (check.lower() if original[-1] == "x" else check)
        return self._generate_generic(original, EntityType.IDCN)
    
    def _generate_org(self, original: str) -> str:
        """Generate masked organization name."""
        counter = self._counters[EntityType.ORG]
        self._counters[EntityType.ORG] += 1
        
        # Use length-preserving format
        length = len(original)
        base = f"组织{counter:02d}"
        
        # Pad or truncate
        if len(base) < length:
            base += "X" * (length - len(base))
        return base[:length]
    
    def _generate_address(self, original: str) -> str:
        """Generate masked address."""
        counter = self._counters[EntityType.ADDRESS]
        self._counters[EntityType.ADDRESS] += 1
        
        length = len(original)
        # Preserve length and rough character classes to avoid breaking layouts.
        # (Exact restoration uses vault/session mappings.)
        out = []
        for ch in original:
            if "\u4e00" <= ch <= "\u9fff":
                out.append("某")
            elif ch.isdigit():
                out.append("0")
            elif ch.isalpha():
                out.append("X")
            else:
                out.append(ch)

        result = "".join(out)
        # Ensure uniqueness-friendly variation by tweaking digit positions first.
        chars = list(result)
        digit_positions = [i for i, ch in enumerate(chars) if ch.isdigit()]
        suffix = f"{counter:04d}"
        if digit_positions:
            k = min(4, len(digit_positions))
            for j in range(k):
                chars[digit_positions[-k + j]] = suffix[-k + j]
        return "".join(chars)[:length]

    def _generate_date_time(self, original: str) -> str:
        """Generate masked date/time string while preserving separators."""
        counter = self._counters[EntityType.DATE_TIME]
        self._counters[EntityType.DATE_TIME] += 1

        digits = [c for c in original if c.isdigit()]
        if not digits:
            return self._generate_generic(original, EntityType.DATE_TIME)

        seed = f"{counter:0{len(digits)}d}"[-len(digits):]
        out = []
        di = 0
        for ch in original:
            if ch.isdigit():
                out.append(seed[di])
                di += 1
            else:
                out.append(ch)
        return "".join(out)
    
    def _generate_generic(self, original: str, entity_type: EntityType) -> str:
        """Generate generic placeholder (length-preserving, unique per type)."""
        counter = self._counters[entity_type]
        self._counters[entity_type] += 1

        length = len(original)
        if length <= 0:
            return original

        # Prefer digits-only masks for numeric strings.
        if original.isdigit():
            return f"{counter:0{length}d}"[-length:]

        # Base36 counter to fit short strings.
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def to_base36(n: int) -> str:
            if n == 0:
                return "0"
            chars = []
            while n > 0:
                n, r = divmod(n, 36)
                chars.append(alphabet[r])
            return "".join(reversed(chars))

        suffix = to_base36(counter)
        if len(suffix) > length:
            suffix = suffix[-length:]

        pad_char = "＊"  # fullwidth asterisk, more CJK-friendly
        return (pad_char * (length - len(suffix))) + suffix
    
    def get_reverse_mappings(self) -> Dict[str, str]:
        """Get reverse mappings {masked -> original} for restoration."""
        return {v: k[0] for k, v in self._cache.items()}
    
    def clear_cache(self):
        """Clear the cache (start fresh)."""
        self._cache.clear()
        self._counters = {t: 0 for t in EntityType}
