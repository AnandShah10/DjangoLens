import ast
import os
import subprocess
import tempfile
import unittest

from django_doctor.analyzers.api_diff import (
    extract_serializers,
    diff_serializers_file,
    scan_api_diff,
    _is_git_repo,
    _resolve_meta_fields,
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(cwd):
    _git(["init", "-q"], cwd)
    _git(["config", "user.email", "test@test.com"], cwd)
    _git(["config", "user.name", "Test"], cwd)


def _commit(cwd, message="commit"):
    _git(["add", "-A"], cwd)
    _git(["commit", "-q", "-m", message], cwd)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _write(cwd, relative_path, content):
    full_path = os.path.join(cwd, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)


_SERIALIZER_V1 = """from rest_framework import serializers


class OrderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    total = serializers.DecimalField(max_digits=10, decimal_places=2)
    notes = serializers.CharField(required=False)
"""

_SERIALIZER_V2 = """from rest_framework import serializers


class OrderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    total = serializers.CharField(max_digits=10, decimal_places=2)
    notes = serializers.CharField(required=False)
    currency = serializers.CharField()
    archived = serializers.BooleanField(required=False, default=False)
"""


class ExtractSerializersTests(unittest.TestCase):
    def test_extracts_fields_and_required_defaults(self):
        serializers = extract_serializers(_SERIALIZER_V1, "serializers.py")
        fields = serializers["OrderSerializer"]
        self.assertEqual(fields["id"], {"type": "IntegerField", "required": True})
        self.assertEqual(fields["notes"], {"type": "CharField", "required": False})

    def test_default_kwarg_implies_not_required(self):
        source = (
            "from rest_framework import serializers\n\n"
            "class S(serializers.Serializer):\n"
            "    archived = serializers.BooleanField(default=False)\n"
        )
        fields = extract_serializers(source, "s.py")["S"]
        self.assertFalse(fields["archived"]["required"])

    def test_non_serializer_class_is_ignored(self):
        source = "class NotASerializer:\n    x = 1\n"
        self.assertEqual(extract_serializers(source, "s.py"), {})

    def test_broken_source_does_not_crash(self):
        self.assertEqual(extract_serializers("class S(:\n bad", "s.py"), {})


class ResolveMetaFieldsTests(unittest.TestCase):
    """Direct unit tests for the pure function at the core of the
    Meta.fields resolution — no git, no files, just AST in and a dict
    out."""

    def test_resolves_requested_fields_from_the_model(self):
        meta_fields = ast.parse("['id', 'name', 'email']", mode="eval").body
        resolved = _resolve_meta_fields(_MODEL_SERIALIZER_MODEL_V1, "User", meta_fields)

        self.assertEqual(resolved["name"], {"type": "CharField", "required": True})
        self.assertEqual(resolved["email"], {"type": "EmailField", "required": True})
        self.assertEqual(resolved["id"], {"type": "IntegerField", "required": False})

    def test_all_shorthand_includes_every_model_field_plus_id(self):
        meta_fields = ast.parse("'__all__'", mode="eval").body
        resolved = _resolve_meta_fields(_MODEL_SERIALIZER_MODEL_V1, "User", meta_fields)
        self.assertEqual(set(resolved.keys()), {"id", "name", "email"})

    def test_field_with_null_true_is_not_required(self):
        source = (
            "from django.db import models\n\n"
            "class User(models.Model):\n"
            "    nickname = models.CharField(max_length=50, null=True)\n"
        )
        meta_fields = ast.parse("['nickname']", mode="eval").body
        resolved = _resolve_meta_fields(source, "User", meta_fields)
        self.assertFalse(resolved["nickname"]["required"])

    def test_field_with_default_is_not_required(self):
        source = (
            "from django.db import models\n\n"
            "class User(models.Model):\n"
            "    active = models.BooleanField(default=True)\n"
        )
        meta_fields = ast.parse("['active']", mode="eval").body
        resolved = _resolve_meta_fields(source, "User", meta_fields)
        self.assertFalse(resolved["active"]["required"])

    def test_missing_model_source_returns_empty_dict(self):
        meta_fields = ast.parse("['id']", mode="eval").body
        self.assertEqual(_resolve_meta_fields(None, "User", meta_fields), {})

    def test_model_class_not_found_returns_empty_dict(self):
        meta_fields = ast.parse("['id']", mode="eval").body
        resolved = _resolve_meta_fields(_MODEL_SERIALIZER_MODEL_V1, "NoSuchModel", meta_fields)
        self.assertEqual(resolved, {})

    def test_field_name_not_on_model_is_silently_skipped(self):
        meta_fields = ast.parse("['id', 'name', 'nonexistent_field']", mode="eval").body
        resolved = _resolve_meta_fields(_MODEL_SERIALIZER_MODEL_V1, "User", meta_fields)
        self.assertNotIn("nonexistent_field", resolved)
        self.assertIn("name", resolved)

    def test_unrecognized_fields_shape_returns_empty_dict(self):
        # e.g. `fields = get_dynamic_fields()` — a function call, not a
        # literal list or "__all__" — must not be guessed at.
        meta_fields = ast.parse("get_dynamic_fields()", mode="eval").body
        resolved = _resolve_meta_fields(_MODEL_SERIALIZER_MODEL_V1, "User", meta_fields)
        self.assertEqual(resolved, {})

    def test_broken_model_source_does_not_crash(self):
        meta_fields = ast.parse("['id']", mode="eval").body
        resolved = _resolve_meta_fields("class User(:\n bad", "User", meta_fields)
        self.assertEqual(resolved, {})


class GitDiffEndToEndTests(unittest.TestCase):
    """Uses a real git repository (not mocks) to verify the full pipeline:
    git show -> ast parse -> field comparison."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        _init_repo(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_drift_scenario_against_real_git_history(self):
        _write(self.repo, "orders/serializers.py", _SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")

        _write(self.repo, "orders/serializers.py", _SERIALIZER_V2)
        # Left uncommitted on purpose — diff_serializers_file's head_ref=None
        # means "current working tree", matching `git diff <base>` semantics.

        results = diff_serializers_file(self.repo, "orders/serializers.py", base_sha, head_ref=None)
        messages = {r["message"] for r in results}

        self.assertEqual(len(results), 4)
        self.assertTrue(any("removed" in m and "'name'" in m for m in messages))
        self.assertTrue(any("type changed from DecimalField to CharField" in m for m in messages))
        self.assertTrue(any("New required field 'currency'" in m for m in messages))
        self.assertTrue(any("Field 'archived' added" in m for m in messages))

    def test_removed_field_is_error_severity(self):
        _write(self.repo, "orders/serializers.py", _SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "orders/serializers.py", _SERIALIZER_V2)

        results = diff_serializers_file(self.repo, "orders/serializers.py", base_sha, head_ref=None)
        removed = next(r for r in results if "removed" in r["message"])
        self.assertEqual(removed["severity"], "error")
        self.assertEqual(removed["confidence"], "static")

    def test_new_optional_field_is_information_severity(self):
        _write(self.repo, "orders/serializers.py", _SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "orders/serializers.py", _SERIALIZER_V2)

        results = diff_serializers_file(self.repo, "orders/serializers.py", base_sha, head_ref=None)
        added = next(r for r in results if "archived" in r["message"])
        self.assertEqual(added["severity"], "information")

    def test_field_becoming_optional_is_informational_not_breaking(self):
        _write(self.repo, "s.py", (
            "from rest_framework import serializers\n\n"
            "class S(serializers.Serializer):\n"
            "    x = serializers.CharField()\n"
        ))
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "s.py", (
            "from rest_framework import serializers\n\n"
            "class S(serializers.Serializer):\n"
            "    x = serializers.CharField(required=False)\n"
        ))

        results = diff_serializers_file(self.repo, "s.py", base_sha, head_ref=None)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["severity"], "information")
        self.assertIn("no longer required", results[0]["message"])

    def test_field_becoming_required_is_warning(self):
        _write(self.repo, "s.py", (
            "from rest_framework import serializers\n\n"
            "class S(serializers.Serializer):\n"
            "    x = serializers.CharField(required=False)\n"
        ))
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "s.py", (
            "from rest_framework import serializers\n\n"
            "class S(serializers.Serializer):\n"
            "    x = serializers.CharField()\n"
        ))

        results = diff_serializers_file(self.repo, "s.py", base_sha, head_ref=None)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["severity"], "warning")
        self.assertIn("became required", results[0]["message"])

    def test_no_changes_produces_no_diagnostics(self):
        _write(self.repo, "s.py", _SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")
        # working tree left identical to base_sha
        results = diff_serializers_file(self.repo, "s.py", base_sha, head_ref=None)
        self.assertEqual(results, [])

    def test_comparing_two_committed_revisions_not_just_working_tree(self):
        _write(self.repo, "s.py", _SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "s.py", _SERIALIZER_V2)
        head_sha = _commit(self.repo, "v2")

        results = diff_serializers_file(self.repo, "s.py", base_sha, head_ref=head_sha)
        self.assertEqual(len(results), 4)

    def test_file_that_did_not_exist_at_base_ref_is_not_diffed(self):
        _write(self.repo, "unrelated.py", "x = 1\n")
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "new_serializers.py", _SERIALIZER_V1)

        results = diff_serializers_file(self.repo, "new_serializers.py", base_sha, head_ref=None)
        self.assertEqual(results, [])

    def test_is_git_repo_detects_real_repo(self):
        self.assertTrue(_is_git_repo(self.repo))

    def test_is_git_repo_false_for_non_repo_directory(self):
        with tempfile.TemporaryDirectory() as not_a_repo:
            self.assertFalse(_is_git_repo(not_a_repo))

    def test_scan_api_diff_returns_error_for_non_git_directory(self):
        with tempfile.TemporaryDirectory() as not_a_repo:
            diagnostics, error = scan_api_diff(not_a_repo, "HEAD")
            self.assertEqual(diagnostics, [])
            self.assertIsNotNone(error)

    def test_scan_api_diff_finds_serializers_across_discovered_apps(self):
        _write(self.repo, "orders/serializers.py", _SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "orders/serializers.py", _SERIALIZER_V2)

        diagnostics, error = scan_api_diff(self.repo, base_sha)
        self.assertIsNone(error)
        self.assertEqual(len(diagnostics), 4)

    def test_scan_api_diff_finds_serializer_only_app_with_no_models_py(self):
        # A serializers-only "api" app (referencing models defined
        # elsewhere) is a real, common DRF pattern — this must not be
        # silently skipped just because the directory has no models.py.
        _write(self.repo, "api/serializers.py", _SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "api/serializers.py", _SERIALIZER_V2)

        diagnostics, error = scan_api_diff(self.repo, base_sha)
        self.assertIsNone(error)
        self.assertEqual(len(diagnostics), 4)


_MODEL_SERIALIZER_MODEL_V1 = """from django.db import models


class User(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
"""

_MODEL_SERIALIZER_SERIALIZER_V1 = """from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email"]
"""

_MODEL_SERIALIZER_MODEL_V2 = """from django.db import models


class User(models.Model):
    name = models.CharField(max_length=100)
    email = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
"""

_MODEL_SERIALIZER_SERIALIZER_V2 = """from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email", "phone"]
"""


class ModelSerializerMetaFieldsTests(unittest.TestCase):
    """Covers the previously-documented gap: a ModelSerializer that only
    lists field NAMES in Meta.fields (no explicit Field() declarations)
    used to be invisible to comparison entirely — these fields now
    resolve against the actual model, fetched at the SAME git ref as the
    serializer being compared."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        _init_repo(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def _commit_v1_and_mutate_to_v2(self):
        _write(self.repo, "api/models.py", _MODEL_SERIALIZER_MODEL_V1)
        _write(self.repo, "api/serializers.py", _MODEL_SERIALIZER_SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")
        _write(self.repo, "api/models.py", _MODEL_SERIALIZER_MODEL_V2)
        _write(self.repo, "api/serializers.py", _MODEL_SERIALIZER_SERIALIZER_V2)
        return base_sha

    def test_type_change_detected_via_meta_fields_alone(self):
        # Exactly the scenario that was previously invisible: no explicit
        # Field() objects anywhere, only Meta.fields names.
        base_sha = self._commit_v1_and_mutate_to_v2()
        results = diff_serializers_file(self.repo, "api/serializers.py", base_sha, head_ref=None)
        messages = {r["message"] for r in results}

        self.assertTrue(any("type changed from EmailField to CharField" in m for m in messages))
        self.assertTrue(any("New required field 'phone'" in m for m in messages))
        self.assertEqual(len(results), 2)

    def test_id_field_from_all_shorthand_is_never_required(self):
        _write(self.repo, "api/models.py", _MODEL_SERIALIZER_MODEL_V1)
        _write(self.repo, "api/serializers.py", (
            "from rest_framework import serializers\n\n"
            "from .models import User\n\n\n"
            "class UserSerializer(serializers.ModelSerializer):\n"
            "    class Meta:\n"
            "        model = User\n"
            "        fields = '__all__'\n"
        ))
        base_sha = _commit(self.repo, "v1")
        # No changes at all — 'id' resolving as required would spuriously
        # show up as "removed" or similar if handled wrong; confirm clean.
        results = diff_serializers_file(self.repo, "api/serializers.py", base_sha, head_ref=None)
        self.assertEqual(results, [])

    def test_explicit_field_declaration_overrides_meta_resolved_one(self):
        # If the serializer ALSO explicitly declares a field that's in
        # Meta.fields, the explicit declaration must win (it's what DRF
        # itself does — Meta.fields just controls inclusion, explicit
        # Field() objects override the auto-generated one). The model's
        # email field is EmailField; the serializer explicitly overrides
        # it as CharField — that override must be what's compared.
        serializer_source = (
            "from rest_framework import serializers\n\n"
            "from .models import User\n\n\n"
            "class UserSerializer(serializers.ModelSerializer):\n"
            "    email = serializers.CharField()\n\n"
            "    class Meta:\n"
            "        model = User\n"
            "        fields = ['id', 'name', 'email']\n"
        )

        parsed = extract_serializers(
            serializer_source,
            "api/serializers.py",
            model_source_fetcher=lambda _filename: _MODEL_SERIALIZER_MODEL_V1,
        )

        self.assertEqual(parsed["UserSerializer"]["email"]["type"], "CharField")
        # 'name' has no explicit declaration, so it's the Meta-resolved
        # one from the model (CharField there too, but prove it resolved
        # at all — not just coincidentally matching).
        self.assertIn("name", parsed["UserSerializer"])
        self.assertEqual(parsed["UserSerializer"]["name"]["type"], "CharField")

    def test_missing_model_file_does_not_crash(self):
        # api/models.py never created at all.
        _write(self.repo, "api/serializers.py", _MODEL_SERIALIZER_SERIALIZER_V1)
        base_sha = _commit(self.repo, "v1")
        results = diff_serializers_file(self.repo, "api/serializers.py", base_sha, head_ref=None)
        self.assertEqual(results, [])

    def test_meta_fields_resolution_is_opt_in_via_fetcher_argument(self):
        from django_doctor.analyzers.api_diff import extract_serializers
        parsed_without_fetcher = extract_serializers(
            _MODEL_SERIALIZER_SERIALIZER_V1, "serializers.py", model_source_fetcher=None
        )
        # No fetcher given -> Meta.fields is never resolved; the
        # serializer has no explicit Field() declarations, so it ends up
        # with zero known fields rather than guessing.
        self.assertEqual(parsed_without_fetcher["UserSerializer"], {})


if __name__ == "__main__":
    unittest.main()
