import ast
import os
import tempfile
import unittest

from django_doctor.discovery import discover_apps
from django_doctor.analyzers.admin import analyze_admin_file, scan_admin, _find_model_fields

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")


class AdminUxAnalyzerTests(unittest.TestCase):
    def test_decorator_registration_missing_list_display_is_flagged(self):
        app_path = os.path.join(FIXTURE_ROOT, "orders")
        diagnostics = analyze_admin_file("orders", app_path, os.path.join(app_path, "admin.py"))
        list_display_diags = [d for d in diagnostics if "list_display" in d["message"]]

        self.assertEqual(len(list_display_diags), 1)
        diag = list_display_diags[0]
        self.assertEqual(diag["rule_id"], "DJANGO007")
        self.assertEqual(diag["severity"], "information")
        self.assertEqual(diag["model"], "Order")
        self.assertIn("OrderAdmin", diag["message"])

    def test_suggested_fix_uses_real_model_fields(self):
        app_path = os.path.join(FIXTURE_ROOT, "orders")
        diagnostics = analyze_admin_file("orders", app_path, os.path.join(app_path, "admin.py"))
        list_display_fix = next(d for d in diagnostics if "list_display" in d["message"])["suggested_fix"]

        # Order's real fields (from orders/models.py) are customer and total.
        self.assertIn("'id'", list_display_fix)
        self.assertIn("'customer'", list_display_fix)
        self.assertIn("'total'", list_display_fix)

    def test_missing_list_filter_flagged_when_model_has_foreign_key(self):
        # Order.customer is a ForeignKey — a real, common list_filter candidate.
        app_path = os.path.join(FIXTURE_ROOT, "orders")
        diagnostics = analyze_admin_file("orders", app_path, os.path.join(app_path, "admin.py"))
        list_filter_diags = [d for d in diagnostics if "list_filter" in d["message"]]

        self.assertEqual(len(list_filter_diags), 1)
        self.assertIn("customer", list_filter_diags[0]["suggested_fix"])

    def test_list_filter_suggestion_is_a_real_tuple_not_a_parenthesized_string(self):
        # Regression test: ('customer') is just a string in parentheses,
        # NOT a tuple — it needs a trailing comma, ('customer',), to
        # actually be one. Caught via manual verification before this
        # test existed; must never regress.
        app_path = os.path.join(FIXTURE_ROOT, "orders")
        diagnostics = analyze_admin_file("orders", app_path, os.path.join(app_path, "admin.py"))
        list_filter_fix = next(d for d in diagnostics if "list_filter" in d["message"])["suggested_fix"]

        tree = ast.parse(list_filter_fix)
        assigned_value = tree.body[0].value
        self.assertIsInstance(assigned_value, ast.Tuple)

    def test_list_filter_suggested_for_choices_field_regardless_of_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "STATUS_CHOICES = [('draft', 'Draft'), ('published', 'Published')]\n\n"
                    "class Article(models.Model):\n"
                    "    status = models.CharField(max_length=20, choices=STATUS_CHOICES)\n"
                    "    priority = models.IntegerField(default=0)\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Article\n\n"
                    "@admin.register(Article)\n"
                    "class ArticleAdmin(admin.ModelAdmin):\n"
                    "    list_display = ('id', 'status')\n"
                )
            diagnostics = analyze_admin_file("app", tmp, admin_path)
            list_filter_fix = next(d for d in diagnostics if "list_filter" in d["message"])["suggested_fix"]

            # 'status' has choices= -> candidate even though CharField
            # itself isn't in the fixed Boolean/FK/M2M type list.
            self.assertIn("status", list_filter_fix)
            # 'priority' has no choices= and isn't Boolean/FK/M2M -> not a candidate.
            self.assertNotIn("priority", list_filter_fix)

    def test_list_display_present_but_list_filter_still_suggested(self):
        # PaymentAdmin already has list_display, but not list_filter —
        # Payment.order is a ForeignKey, a real candidate. The two checks
        # are independent; having one configured doesn't suppress the other.
        app_path = os.path.join(FIXTURE_ROOT, "payments")
        diagnostics = analyze_admin_file("payments", app_path, os.path.join(app_path, "admin.py"))

        self.assertEqual(len(diagnostics), 1)
        self.assertIn("list_filter", diagnostics[0]["message"])
        self.assertIn("order", diagnostics[0]["suggested_fix"])
        self.assertNotIn("list_display", diagnostics[0]["message"])

    def test_scan_admin_covers_whole_fixture_project(self):
        apps = discover_apps(FIXTURE_ROOT)
        diagnostics = scan_admin(apps)

        # orders: list_display + list_filter. payments: list_filter +
        # autocomplete_fields (Payment.order -> Order, which IS registered
        # via OrderAdmin elsewhere in the project — this is the whole-point
        # of autocomplete_fields needing project-wide context).
        self.assertEqual(len(diagnostics), 4)
        self.assertEqual({d["app"] for d in diagnostics}, {"orders", "payments"})

    def test_autocomplete_fields_suggested_when_related_model_is_registered_elsewhere(self):
        apps = discover_apps(FIXTURE_ROOT)
        diagnostics = scan_admin(apps)
        autocomplete_diags = [d for d in diagnostics if "autocomplete_fields" in d["message"]]

        self.assertEqual(len(autocomplete_diags), 1)
        diag = autocomplete_diags[0]
        self.assertIn("PaymentAdmin", diag["message"])
        self.assertIn("order", diag["suggested_fix"])

    def test_autocomplete_fields_not_suggested_when_related_model_is_unregistered(self):
        # Order.customer -> User, and users/ has no admin.py at all in the
        # fixture, so User is never registered — must not be suggested.
        apps = discover_apps(FIXTURE_ROOT)
        diagnostics = scan_admin(apps)
        order_diags = [d for d in diagnostics if d.get("model") == "Order"]

        self.assertFalse(any("autocomplete_fields" in d["message"] for d in order_diags))

    def test_analyze_admin_file_alone_never_sees_other_files_registrations(self):
        # Calling analyze_admin_file directly (no registered_model_names
        # passed) must not fabricate an autocomplete_fields suggestion —
        # it has no way to know Order is registered elsewhere without
        # that explicit whole-project context.
        app_path = os.path.join(FIXTURE_ROOT, "payments")
        diagnostics = analyze_admin_file("payments", app_path, os.path.join(app_path, "admin.py"))
        self.assertFalse(any("autocomplete_fields" in d["message"] for d in diagnostics))

    def test_autocomplete_fields_respects_explicitly_passed_registered_names(self):
        app_path = os.path.join(FIXTURE_ROOT, "payments")
        diagnostics = analyze_admin_file(
            "payments", app_path, os.path.join(app_path, "admin.py"),
            registered_model_names={"Order"},
        )
        self.assertTrue(any("autocomplete_fields" in d["message"] for d in diagnostics))

    def test_self_referential_foreign_key_is_never_an_autocomplete_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "class Category(models.Model):\n"
                    "    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True)\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Category\n\n"
                    "@admin.register(Category)\n"
                    "class CategoryAdmin(admin.ModelAdmin):\n"
                    "    list_display = ('id', 'parent')\n"
                    "    list_filter = ('parent',)\n"
                    "    search_fields = ()\n"
                )
            # Even if "Category" itself is (trivially) registered, 'self'
            # must never surface as an autocomplete_fields suggestion.
            diagnostics = analyze_admin_file(
                "app", tmp, admin_path, registered_model_names={"Category"}
            )
            self.assertFalse(any("autocomplete_fields" in d["message"] for d in diagnostics))

    def test_no_list_filter_candidates_means_no_suggestion(self):
        # A model with only an IntegerField (no Boolean/FK/M2M) has no
        # list_filter candidates — must not fabricate one out of nothing.
        # Deliberately avoids CharField/TextField/etc. here too, since
        # those are now search_fields candidates and would add noise to
        # what this test is checking.
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "class Plain(models.Model):\n"
                    "    count = models.IntegerField()\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Plain\n\n"
                    "@admin.register(Plain)\n"
                    "class PlainAdmin(admin.ModelAdmin):\n"
                    "    list_display = ('id', 'count')\n"
                )
            diagnostics = analyze_admin_file("plain_app", tmp, admin_path)
            self.assertEqual(diagnostics, [])

    def test_search_fields_suggested_for_text_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "class Article(models.Model):\n"
                    "    title = models.CharField(max_length=200)\n"
                    "    body = models.TextField()\n"
                    "    published_at = models.DateTimeField()\n"
                    "    created_at = models.DateTimeField(auto_now_add=True)\n"
                    "    updated_at = models.DateTimeField(auto_now=True)\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Article\n\n"
                    "@admin.register(Article)\n"
                    "class ArticleAdmin(admin.ModelAdmin):\n"
                    "    list_display = ('id', 'title')\n"
                    "    list_filter = ()\n"
                )
            self.diagnostics = analyze_admin_file("app", tmp, admin_path)

            search = next(d for d in self.diagnostics if "search_fields" in d["message"])
            self.assertIn("title", search["suggested_fix"])
            self.assertIn("body", search["suggested_fix"])
            self.assertNotIn("published_at", search["suggested_fix"])

    def test_date_hierarchy_suggests_first_date_field_in_source_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "class Article(models.Model):\n"
                    "    published_at = models.DateTimeField()\n"
                    "    created_at = models.DateTimeField(auto_now_add=True)\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Article\n\n"
                    "@admin.register(Article)\n"
                    "class ArticleAdmin(admin.ModelAdmin):\n"
                    "    list_display = ('id',)\n"
                    "    list_filter = ()\n"
                )
            diagnostics = analyze_admin_file("app", tmp, admin_path)
            date_hierarchy = next(d for d in diagnostics if "date_hierarchy" in d["message"])

            # published_at comes first in source order — must be chosen,
            # not created_at (which happens to also be a DateTimeField).
            self.assertEqual(date_hierarchy["suggested_fix"], "date_hierarchy = 'published_at'")

    def test_date_hierarchy_suggestion_is_a_string_not_a_tuple(self):
        # Unlike list_display/list_filter/search_fields/readonly_fields,
        # date_hierarchy takes a single field NAME (a string), not a
        # tuple — the suggested fix must reflect that, not wrap it in ().
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "class Article(models.Model):\n"
                    "    published_at = models.DateTimeField()\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Article\n\n"
                    "@admin.register(Article)\n"
                    "class ArticleAdmin(admin.ModelAdmin):\n"
                    "    list_display = ('id',)\n"
                    "    list_filter = ()\n"
                )
            diagnostics = analyze_admin_file("app", tmp, admin_path)
            date_hierarchy = next(d for d in diagnostics if "date_hierarchy" in d["message"])

            tree = ast.parse(date_hierarchy["suggested_fix"])
            assigned_value = tree.body[0].value
            self.assertIsInstance(assigned_value, ast.Constant)
            self.assertIsInstance(assigned_value.value, str)

    def test_readonly_fields_suggested_only_for_auto_now_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "class Article(models.Model):\n"
                    "    published_at = models.DateTimeField()\n"
                    "    created_at = models.DateTimeField(auto_now_add=True)\n"
                    "    updated_at = models.DateTimeField(auto_now=True)\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Article\n\n"
                    "@admin.register(Article)\n"
                    "class ArticleAdmin(admin.ModelAdmin):\n"
                    "    list_display = ('id',)\n"
                    "    list_filter = ()\n"
                    "    date_hierarchy = 'published_at'\n"
                    "    search_fields = ()\n"
                )
            diagnostics = analyze_admin_file("app", tmp, admin_path)
            readonly = next(d for d in diagnostics if "readonly_fields" in d["message"])

            self.assertIn("created_at", readonly["suggested_fix"])
            self.assertIn("updated_at", readonly["suggested_fix"])
            self.assertNotIn("published_at", readonly["suggested_fix"])

    def test_no_date_or_auto_fields_means_no_date_or_readonly_suggestions(self):
        # A model with only a ForeignKey has no DateField/DateTimeField
        # and no auto_now field — neither suggestion should fire.
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "class Comment(models.Model):\n"
                    "    author = models.ForeignKey('auth.User', on_delete=models.CASCADE)\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Comment\n\n"
                    "@admin.register(Comment)\n"
                    "class CommentAdmin(admin.ModelAdmin):\n"
                    "    list_display = ('id', 'author')\n"
                    "    list_filter = ('author',)\n"
                    "    search_fields = ()\n"
                )
            diagnostics = analyze_admin_file("app", tmp, admin_path)
            messages = {d["message"] for d in diagnostics}

            self.assertFalse(any("date_hierarchy" in m for m in messages))
            self.assertFalse(any("readonly_fields" in m for m in messages))

    def test_app_with_no_admin_py_is_skipped_without_crashing(self):
        # users has no admin.py in the fixture at all.
        apps = [a for a in discover_apps(FIXTURE_ROOT) if a[0] == "users"]
        diagnostics = scan_admin(apps)
        self.assertEqual(diagnostics, [])

    def test_broken_admin_file_does_not_crash_the_scan(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("class Broken(:\n    not valid python")
            path = f.name
        try:
            diagnostics = analyze_admin_file("broken_app", os.path.dirname(path), path)
            self.assertEqual(diagnostics, [])
        finally:
            os.unlink(path)

    def test_same_class_registered_both_ways_is_not_double_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write(
                    "from django.db import models\n\n"
                    "class Widget(models.Model):\n"
                    "    count = models.IntegerField()\n"
                )
            admin_path = os.path.join(tmp, "admin.py")
            with open(admin_path, "w") as f:
                f.write(
                    "from django.contrib import admin\n"
                    "from .models import Widget\n\n"
                    "@admin.register(Widget)\n"
                    "class WidgetAdmin(admin.ModelAdmin):\n"
                    "    pass\n\n"
                    "admin.site.register(Widget, WidgetAdmin)\n"
                )
            diagnostics = analyze_admin_file("widgets", tmp, admin_path)
            # Only list_display should fire (IntegerField isn't a
            # list_filter/search_fields/date_hierarchy/readonly_fields
            # candidate of any kind) — and it must appear once, not twice,
            # despite the double registration.
            self.assertEqual(len(diagnostics), 1)
            self.assertIn("list_display", diagnostics[0]["message"])


class AbstractBaseInheritanceTests(unittest.TestCase):
    """Fields inherited from an abstract base class defined in the same
    models.py must be visible to the Admin UX Analyzer; fields from a
    concrete (multi-table inheritance) base must not be."""

    def _write_app(self, tmp, models_source, admin_source):
        with open(os.path.join(tmp, "models.py"), "w") as f:
            f.write(models_source)
        admin_path = os.path.join(tmp, "admin.py")
        with open(admin_path, "w") as f:
            f.write(admin_source)
        return admin_path

    def test_abstract_base_fields_are_included_before_subclass_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_app(
                tmp,
                "from django.db import models\n\n"
                "class TimestampedModel(models.Model):\n"
                "    created_at = models.DateTimeField(auto_now_add=True)\n"
                "    updated_at = models.DateTimeField(auto_now=True)\n\n"
                "    class Meta:\n"
                "        abstract = True\n\n"
                "class Order(TimestampedModel):\n"
                "    total = models.DecimalField(max_digits=10, decimal_places=2)\n",
                "from django.contrib import admin\n"
                "from .models import Order\n\n"
                "@admin.register(Order)\n"
                "class OrderAdmin(admin.ModelAdmin):\n"
                "    pass\n",
            )
            fields = _find_model_fields(tmp, "Order")
            self.assertEqual([name for name, *_ in fields], ["created_at", "updated_at", "total"])

    def test_readonly_fields_suggested_from_abstract_base_auto_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            admin_path = self._write_app(
                tmp,
                "from django.db import models\n\n"
                "class TimestampedModel(models.Model):\n"
                "    created_at = models.DateTimeField(auto_now_add=True)\n"
                "    updated_at = models.DateTimeField(auto_now=True)\n\n"
                "    class Meta:\n"
                "        abstract = True\n\n"
                "class Order(TimestampedModel):\n"
                "    total = models.DecimalField(max_digits=10, decimal_places=2)\n",
                "from django.contrib import admin\n"
                "from .models import Order\n\n"
                "@admin.register(Order)\n"
                "class OrderAdmin(admin.ModelAdmin):\n"
                "    list_display = ('id', 'total')\n"
                "    list_filter = ()\n"
                "    search_fields = ()\n"
                "    date_hierarchy = 'created_at'\n",
            )
            diagnostics = analyze_admin_file("app", tmp, admin_path)
            readonly = next(d for d in diagnostics if "readonly_fields" in d["message"])
            self.assertIn("created_at", readonly["suggested_fix"])
            self.assertIn("updated_at", readonly["suggested_fix"])

    def test_concrete_base_fields_are_not_followed(self):
        # Multi-table inheritance: Place's fields live on Place's own
        # table, not copied onto Restaurant — must not appear here.
        with tempfile.TemporaryDirectory() as tmp:
            self._write_app(
                tmp,
                "from django.db import models\n\n"
                "class Place(models.Model):\n"
                "    name = models.CharField(max_length=50)\n\n"
                "class Restaurant(Place):\n"
                "    serves_pizza = models.BooleanField(default=False)\n",
                "",
            )
            fields = _find_model_fields(tmp, "Restaurant")
            self.assertEqual([name for name, *_ in fields], ["serves_pizza"])

    def test_multi_level_abstract_inheritance_is_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_app(
                tmp,
                "from django.db import models\n\n"
                "class Base(models.Model):\n"
                "    created_at = models.DateTimeField(auto_now_add=True)\n"
                "    class Meta:\n"
                "        abstract = True\n\n"
                "class Named(Base):\n"
                "    name = models.CharField(max_length=50)\n"
                "    class Meta:\n"
                "        abstract = True\n\n"
                "class Thing(Named):\n"
                "    weight = models.IntegerField()\n",
                "",
            )
            fields = _find_model_fields(tmp, "Thing")
            self.assertEqual([name for name, *_ in fields], ["created_at", "name", "weight"])

    def test_cyclic_inheritance_terminates_without_hanging(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_app(
                tmp,
                "from django.db import models\n\n"
                "class A(B):\n"
                "    x = models.IntegerField()\n"
                "    class Meta:\n"
                "        abstract = True\n\n"
                "class B(A):\n"
                "    y = models.IntegerField()\n"
                "    class Meta:\n"
                "        abstract = True\n\n"
                "class C(A):\n"
                "    z = models.IntegerField()\n",
                "",
            )
            # Invalid in real Python/Django, but a user's broken/in-progress
            # file must never hang the scan — just terminate.
            fields = _find_model_fields(tmp, "C")
            self.assertIn("z", [name for name, *_ in fields])

    def test_base_class_defined_in_another_file_is_not_followed(self):
        # Documented limitation: only same-file abstract bases are
        # resolved. The model still resolves (its own fields), it just
        # doesn't see the unreachable base's fields.
        with tempfile.TemporaryDirectory() as tmp:
            self._write_app(
                tmp,
                "from django.db import models\n"
                "from core.models import TimestampedModel\n\n"
                "class Order(TimestampedModel):\n"
                "    total = models.DecimalField(max_digits=10, decimal_places=2)\n",
                "",
            )
            fields = _find_model_fields(tmp, "Order")
            self.assertEqual([name for name, *_ in fields], ["total"])


if __name__ == "__main__":
    unittest.main()
