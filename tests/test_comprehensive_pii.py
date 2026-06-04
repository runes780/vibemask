"""
Comprehensive PII Detection and Restoration Tests.

Tests all PII types defined in Chinese personal information regulations:
- 姓名 (PERSON)
- 出生日期 (DATE_TIME)
- 身份证号码 (IDCN)
- 住址 (ADDRESS)
- 电话号码 (PHONE)
- 电子邮箱 (EMAIL)
- URL
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class TestPlaceholderGeneration:
    """Test placeholder generation for reversible masking."""
    
    def setup_method(self):
        from vibemask.masker.placeholder import PlaceholderGenerator
        self.generator = PlaceholderGenerator()
    
    def test_person_name_2char(self):
        """2-character Chinese name (surname + 1 char)."""
        from vibemask.core.span import EntityType
        original = "张三"
        masked = self.generator.generate(original, EntityType.PERSON)
        assert masked == "{{PERSON_000001}}"
        assert masked != original, "Masked should differ from original"
    
    def test_person_name_3char(self):
        """3-character Chinese name (most common)."""
        from vibemask.core.span import EntityType
        original = "李明华"
        masked = self.generator.generate(original, EntityType.PERSON)
        assert masked == "{{PERSON_000001}}"
    
    def test_person_name_4char_compound(self):
        """4-character name with compound surname."""
        from vibemask.core.span import EntityType
        original = "欧阳小雪"
        masked = self.generator.generate(original, EntityType.PERSON)
        assert masked == "{{PERSON_000001}}"
    
    def test_phone_11_digits(self):
        """11-digit Chinese mobile number."""
        from vibemask.core.span import EntityType
        original = "13812345678"
        masked = self.generator.generate(original, EntityType.PHONE)
        assert len(masked) == len(original)
        assert masked.isdigit(), "Phone mask should be all digits"
    
    def test_phone_with_separators(self):
        """Phone number with separator preservation."""
        from vibemask.core.span import EntityType
        original = "138-1234-5678"
        masked = self.generator.generate(original, EntityType.PHONE)
        assert len(masked) == len(original)
        assert masked[3] == "-", "First separator should be preserved"
        assert masked[8] == "-", "Second separator should be preserved"
    
    def test_email_format(self):
        """Email address with @ and domain preserved."""
        from vibemask.core.span import EntityType
        original = "test@example.com"
        masked = self.generator.generate(original, EntityType.EMAIL)
        assert "@" in masked, "Email should contain @"
        assert masked.endswith("@example.com"), "Domain should be preserved"
        assert len(masked) == len(original)
    
    def test_idcn_18_digits(self):
        """18-digit Chinese ID card number."""
        from vibemask.core.span import EntityType
        original = "110101198801011234"
        masked = self.generator.generate(original, EntityType.IDCN)
        assert len(masked) == len(original)
    
    def test_idcn_with_x(self):
        """18-digit ID card ending with X."""
        from vibemask.core.span import EntityType
        original = "11010119880101123X"
        masked = self.generator.generate(original, EntityType.IDCN)
        assert len(masked) == len(original)
    
    def test_address_preserves_structure(self):
        """Address masking preserves character classes."""
        from vibemask.core.span import EntityType
        original = "北京市朝阳区xxx路123号"
        masked = self.generator.generate(original, EntityType.ADDRESS)
        assert len(masked) == len(original)
    
    def test_date_time_with_dashes(self):
        """Date with dash separators."""
        from vibemask.core.span import EntityType
        original = "2024-01-15"
        masked = self.generator.generate(original, EntityType.DATE_TIME)
        assert len(masked) == len(original)
        assert masked[4] == "-" and masked[7] == "-", "Date separators should be preserved"
    
    def test_date_time_chinese_format(self):
        """Date in Chinese format."""
        from vibemask.core.span import EntityType
        original = "2024年01月15日"
        masked = self.generator.generate(original, EntityType.DATE_TIME)
        assert len(masked) == len(original)
    
    def test_url_format(self):
        """URL masking preserves protocol and domain."""
        from vibemask.core.span import EntityType
        original = "https://example.com/user/123"
        masked = self.generator.generate(original, EntityType.URL)
        assert masked.startswith("https://example.com"), "URL protocol and domain preserved"

    def test_account_number_generic_mask(self):
        """Privacy Filter account_number type gets a length-preserving placeholder."""
        from vibemask.core.span import EntityType
        original = "4111111111111111"
        masked = self.generator.generate(original, EntityType.ACCOUNT_NUMBER)
        assert len(masked) == len(original)
        assert masked.isdigit()

    def test_secret_generic_mask(self):
        """Privacy Filter secret type gets a length-preserving placeholder."""
        from vibemask.core.span import EntityType
        original = "sk-test-secret"
        masked = self.generator.generate(original, EntityType.SECRET)
        assert len(masked) == len(original)
        assert masked != original
    
    def test_reverse_mappings(self):
        """Verify reverse mappings are created correctly."""
        from vibemask.core.span import EntityType
        originals = ["张三", "13812345678", "test@example.com"]
        types = [EntityType.PERSON, EntityType.PHONE, EntityType.EMAIL]
        
        for orig, t in zip(originals, types):
            self.generator.generate(orig, t)
        
        reverse = self.generator.get_reverse_mappings()
        assert len(reverse) == 3, "Should have 3 reverse mappings"
        
        for masked, original in reverse.items():
            assert original in originals, f"Original {original} should be in test data"


class TestVaultRoundtrip:
    """Test that vault storage enables lossless roundtrip."""
    
    def test_vault_consistent_mapping(self, tmp_path: Path, monkeypatch):
        """Same original should always get same masked value."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from vibemask.vault.storage import VaultStorage
        vault = VaultStorage(str(tmp_path / "project"))
        
        masked1 = vault.get_or_create_mapping(
            original="张三",
            entity_type="PERSON",
            masked="{{PERSON_000001}}"
        )
        masked2 = vault.get_or_create_mapping(
            original="张三",
            entity_type="PERSON",
            masked="{{PERSON_000001}}"
        )
        
        assert masked1 == masked2, "Same original should return same masked"
    
    def test_vault_unique_masked_values(self, tmp_path: Path, monkeypatch):
        """Different originals with same proposed mask should get unique masked values."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from vibemask.vault.storage import VaultStorage
        vault = VaultStorage(str(tmp_path / "project"))
        
        masked1 = vault.get_or_create_mapping(
            original="张三",
            entity_type="PERSON",
            masked="{{PERSON_000001}}"
        )
        masked2 = vault.get_or_create_mapping(
            original="李四",
            entity_type="PERSON",
            masked="{{PERSON_000001}}"  # Same proposed mask
        )
        
        assert masked1 != masked2, "Different originals should get unique masked values"
        
        # Verify reverse lookup is unambiguous
        assert vault.get_mapping_by_masked(masked1) == "张三"
        assert vault.get_mapping_by_masked(masked2) == "李四"
    
    def test_vault_full_roundtrip(self, tmp_path: Path, monkeypatch):
        """Test complete mask -> restore roundtrip via vault."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from vibemask.vault.storage import VaultStorage
        vault = VaultStorage(str(tmp_path / "project"))
        
        test_data = [
            ("张三", "PERSON", "{{PERSON_000001}}"),
            ("13812345678", "PHONE", "13900000000"),
            ("test@example.com", "EMAIL", "u000@example.com"),
        ]
        
        # Mask phase
        original_to_masked = {}
        for original, entity_type, proposed_mask in test_data:
            masked = vault.get_or_create_mapping(
                original=original,
                entity_type=entity_type,
                masked=proposed_mask
            )
            original_to_masked[original] = masked
        
        # Restore phase
        all_mappings = vault.get_all_mappings()  # masked -> original
        
        for original, masked in original_to_masked.items():
            restored = all_mappings.get(masked)
            assert restored == original, f"Roundtrip failed: {original} -> {masked} -> {restored}"


class TestFalsePositivePrevention:
    """Test that common words are NOT detected as names."""
    
    def test_non_name_words_excluded(self):
        """Words in NON_NAME_WORDS should not be detected as names."""
        from vibemask.detector.smart_detector import NON_NAME_WORDS, SmartChineseNameDetector
        
        detector = SmartChineseNameDetector(use_llm=False)
        
        # These should NOT be detected as names
        non_names = ["高级教师", "高中数学", "单位", "范围", "任务", "程序"]
        
        for word in non_names:
            if word in NON_NAME_WORDS:
                # Verify the word is in exclusion list
                spans = detector.detect(f"这是一个{word}的测试", strict=False)
                detected_texts = [s.text for s in spans]
                assert word not in detected_texts, f"'{word}' should NOT be detected as a name"

    def test_administrative_divisions_excluded(self):
        """Administrative divisions should not be detected as person names."""
        from vibemask.detector.smart_detector import SmartChineseNameDetector

        detector = SmartChineseNameDetector(use_llm=False)
        spans = detector.detect("淳安县列入名单，杭州市同步推进。", strict=False)
        detected_texts = [s.text for s in spans]

        assert "淳安县" not in detected_texts
        assert "杭州市" not in detected_texts


class TestTextRoundtrip:
    """Test text-level mask and restore roundtrip."""
    
    def test_plaintext_roundtrip(self, tmp_path: Path, monkeypatch):
        """Test masking and restoring plain text preserves original."""
        monkeypatch.setenv("HOME", str(tmp_path))
        
        from vibemask.vault.storage import VaultStorage
        from vibemask.masker.placeholder import PlaceholderGenerator
        from vibemask.core.span import EntityType
        
        original_text = "联系人：张三，电话：13812345678，邮箱：zhangsan@example.com"
        
        # Simulate detection results
        entities = [
            ("张三", EntityType.PERSON),
            ("13812345678", EntityType.PHONE),
            ("zhangsan@example.com", EntityType.EMAIL),
        ]
        
        vault = VaultStorage(str(tmp_path / "project"))
        generator = PlaceholderGenerator()
        
        # Build replacements
        replacements = {}
        for original, etype in entities:
            proposed_mask = generator.generate(original, etype)
            final_mask = vault.get_or_create_mapping(
                original=original,
                entity_type=etype.value,
                masked=proposed_mask
            )
            replacements[original] = final_mask
        
        # Apply masking
        masked_text = original_text
        for orig, mask in replacements.items():
            masked_text = masked_text.replace(orig, mask)
        
        # Verify masking worked
        for orig in replacements.keys():
            assert orig not in masked_text, f"Original '{orig}' should not be in masked text"
        
        # Restore
        reverse_mappings = vault.get_all_mappings()
        restored_text = masked_text
        for mask, orig in reverse_mappings.items():
            restored_text = restored_text.replace(mask, orig)
        
        # Verify restoration
        assert restored_text == original_text, f"Roundtrip failed:\nOriginal: {original_text}\nRestored: {restored_text}"
