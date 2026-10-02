import uuid
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from flexcommerce_core import hooks
from flexcommerce_engagement import services, signals
from flexcommerce_engagement.models import (
    ProductQuestion,
    ProductReview,
    RecentlyViewed,
    RecentSearch,
    SearchTerm,
    Wishlist,
    WishlistItem,
)
from tests.models import Product

from .conftest import client_for

pytestmark = pytest.mark.django_db


def review_payload(product, **extra):
    return {
        "product_id": str(product.pk),
        "rating": 4,
        "title": "Nice",
        "body": "Good fabric",
        **extra,
    }


class TestReviews:
    def test_create_requires_moderation(self, user, product):
        resp = client_for(user).post("/api/reviews/", review_payload(product), format="json")
        assert resp.status_code == 201, resp.json()
        assert resp.json()["is_approved"] is False and resp.json()["user_name"] == "Amaka O."
        assert client_for().get(f"/api/reviews/?product_id={product.pk}").json()["count"] == 0

    def test_duplicate_review(self, user, product):
        client_for(user).post("/api/reviews/", review_payload(product), format="json")
        resp = client_for(user).post("/api/reviews/", review_payload(product), format="json")
        assert resp.status_code == 400 and resp.json()["error"] == "already_reviewed"

    def test_validation_and_whitelist(self, user, product):
        c = client_for(user)
        assert c.post("/api/reviews/", review_payload(product, rating=6), format="json").status_code == 400
        assert (
            c.post("/api/reviews/", review_payload(product, product_type="auth.user"), format="json").status_code == 400
        )
        missing = c.post(
            "/api/reviews/",
            {**review_payload(product), "product_id": str(uuid.uuid4())},
            format="json",
        )
        assert missing.status_code == 404
        assert client_for().post("/api/reviews/", review_payload(product), format="json").status_code in (401, 403)

    def test_moderation_and_rating_summary(self, user, other, staff, product):
        ratings = []
        signals.rating_changed.connect(
            lambda sender, **kw: ratings.append((kw["average"], kw["count"])),
            weak=False,
            dispatch_uid="t-rating",
        )
        try:
            a = client_for(user).post("/api/reviews/", review_payload(product, rating=5), format="json").json()
            b = client_for(other).post("/api/reviews/", review_payload(product, rating=2), format="json").json()
            s = client_for(staff)
            pending = s.get("/api/reviews/?status=pending").json()
            assert pending["count"] == 2
            assert client_for(user).post(f"/api/reviews/{a['id']}/approve/").status_code == 403
            assert s.post(f"/api/reviews/{a['id']}/approve/").json()["is_approved"] is True
            s.post(f"/api/reviews/{b['id']}/approve/")
            summary = client_for().get(f"/api/reviews/summary/?product_id={product.pk}").json()
            assert summary == {
                "average": "3.50",
                "count": 2,
                "distribution": {"1": 0, "2": 1, "3": 0, "4": 0, "5": 1},
            }
            assert s.post(f"/api/reviews/{b['id']}/reject/", {"note": "spam"}).json()["is_approved"] is False
        finally:
            signals.rating_changed.disconnect(dispatch_uid="t-rating")
        assert ratings[-1] == (Decimal("5.00"), 1)
        assert s.post("/api/reviews/00000000-0000-0000-0000-000000000000/approve/").status_code == 404
        assert client_for().get("/api/reviews/summary/").status_code == 400

    def test_owner_only_edit_and_delete(self, user, other, staff, product, fc):
        """Users cannot edit or delete other users' approved reviews."""
        fc(
            PRODUCT_MODELS=["tests.Product"],
            THROTTLING_ENABLED=False,
            REVIEWS_REQUIRE_APPROVAL=False,
        )
        rid = client_for(user).post("/api/reviews/", review_payload(product), format="json").json()["id"]
        intruder = client_for(other)
        assert intruder.patch(f"/api/reviews/{rid}/", {"body": "hacked"}, format="json").status_code in (403, 404)
        assert intruder.delete(f"/api/reviews/{rid}/").status_code in (403, 404)
        assert ProductReview.objects.get(pk=rid).body == "Good fabric"
        edited = client_for(user).patch(f"/api/reviews/{rid}/", {"body": "Updated", "rating": 3}, format="json")
        assert edited.status_code == 200 and edited.json()["rating"] == 3
        assert client_for(staff).delete(f"/api/reviews/{rid}/").status_code == 204

    def test_edit_resets_approval_when_moderated(self, user, product):
        review = services.create_review(user, product, 5, "great")
        services.moderate_review(review, True)
        services.update_review(review, body="changed")
        review.refresh_from_db()
        assert review.is_approved is False

    def test_helpful_votes_are_one_per_user(self, user, other, product, fc):
        fc(
            PRODUCT_MODELS=["tests.Product"],
            THROTTLING_ENABLED=False,
            REVIEWS_REQUIRE_APPROVAL=False,
        )
        rid = client_for(user).post("/api/reviews/", review_payload(product), format="json").json()["id"]
        voter = client_for(other)
        assert voter.post(f"/api/reviews/{rid}/helpful/").json() == {
            "voted": True,
            "helpful_count": 1,
        }
        listing = voter.get(f"/api/reviews/?product_id={product.pk}").json()["results"][0]
        assert listing["has_voted"] is True
        assert voter.post(f"/api/reviews/{rid}/helpful/").json() == {
            "voted": False,
            "helpful_count": 0,
        }
        assert client_for(user).post(f"/api/reviews/{rid}/helpful/").status_code == 403

    def test_filters_and_ordering(self, user, other, staff, product, fc):
        fc(
            PRODUCT_MODELS=["tests.Product"],
            THROTTLING_ENABLED=False,
            REVIEWS_REQUIRE_APPROVAL=False,
        )
        services.create_review(user, product, 5, "a")
        services.create_review(other, product, 1, "b")
        base = f"/api/reviews/?product_id={product.pk}"
        assert [r["rating"] for r in client_for().get(base + "&ordering=lowest").json()["results"]] == [1, 5]
        assert [r["rating"] for r in client_for().get(base + "&ordering=highest").json()["results"]] == [5, 1]
        assert client_for().get(base + "&rating=5").json()["count"] == 1
        assert client_for().get(base + "&rating=x").json()["count"] == 0
        assert client_for().get(base + "&verified=1").json()["count"] == 0
        assert client_for().get(base + "&ordering=helpful").json()["count"] == 2
        assert client_for(user).get("/api/reviews/mine/").json()["count"] == 1
        assert client_for(staff).get("/api/reviews/?status=all").json()["count"] == 2

    def test_verified_only_and_verified_purchase(self, user, product, fc):
        fc(PRODUCT_MODELS=["tests.Product"], THROTTLING_ENABLED=False, REVIEWS_VERIFIED_ONLY=True)
        resp = client_for(user).post("/api/reviews/", review_payload(product), format="json")
        assert resp.status_code == 403 and resp.json()["error"] == "purchase_required"
        assert services.has_purchased(user, product) is False

    def test_reply_by_staff_or_vendor(self, user, other, staff, fc):
        fc(
            PRODUCT_MODELS=["tests.Product"],
            THROTTLING_ENABLED=False,
            REVIEWS_REQUIRE_APPROVAL=False,
        )
        vendor_id = uuid.uuid4()
        product = Product.objects.create(vendor_id=vendor_id)
        review = services.create_review(user, product, 3, "ok")
        assert client_for(other).post(f"/api/reviews/{review.pk}/reply/", {"reply": "x"}).status_code == 403

        def vendor_hook(u):
            return vendor_id if u.pk == other.pk else None

        hooks.register("catalog.vendor_id_for_user", vendor_hook)
        try:
            resp = client_for(other).post(f"/api/reviews/{review.pk}/reply/", {"reply": "Thanks!"})
        finally:
            hooks.unregister("catalog.vendor_id_for_user", vendor_hook)
        assert resp.status_code == 200 and resp.json()["reply"] == "Thanks!" and resp.json()["replied_at"]
        assert client_for(staff).post(f"/api/reviews/{review.pk}/reply/", {"reply": "Staff"}).status_code == 200

    def test_admin_moderation_actions(self, client, user, product):
        from django.contrib.auth import get_user_model

        review = services.create_review(user, product, 5, "x")
        client.force_login(get_user_model().objects.create_superuser("root", "r@x.com", "x"))
        client.post(
            "/admin/flexcommerce_engagement/productreview/",
            {"action": "approve_reviews", "_selected_action": [str(review.pk)]},
        )
        review.refresh_from_db()
        assert review.is_approved
        client.post(
            "/admin/flexcommerce_engagement/productreview/",
            {"action": "reject_reviews", "_selected_action": [str(review.pk)]},
        )
        review.refresh_from_db()
        assert review.is_rejected and "rating=5" in str(review)


class TestWishlist:
    def test_default_list_and_add_remove(self, user, product):
        c = client_for(user)
        lists = c.get("/api/wishlists/").json()
        assert len(lists) == 1 and lists[0]["is_default"]
        wid = lists[0]["id"]
        resp = c.post(
            f"/api/wishlists/{wid}/add/",
            {"product_id": str(product.pk), "note": "for Xmas"},
            format="json",
        )
        assert resp.status_code == 201 and resp.json()["added_price"] == "1000.00"
        assert resp.json()["product"]["name"] == "Ankara Dress"
        assert c.post(f"/api/wishlists/{wid}/add/", {"product_id": str(product.pk)}, format="json").status_code == 200
        assert c.delete(f"/api/wishlists/{wid}/remove/{resp.json()['id']}/").status_code == 204
        assert c.delete(f"/api/wishlists/{wid}/").status_code == 400  # default list is protected
        extra = c.post("/api/wishlists/", {"name": "Birthday"}, format="json").json()
        assert c.delete(f"/api/wishlists/{extra['id']}/").status_code == 204

    def test_toggle_and_contains(self, user, product):
        c = client_for(user)
        other_product = Product.objects.create()
        assert c.post("/api/wishlists/toggle/", {"product_id": str(product.pk)}, format="json").json()["wishlisted"]
        found = c.get(f"/api/wishlists/contains/?product_ids={product.pk},{other_product.pk},garbage").json()
        assert found == {"product_ids": [str(product.pk)]}
        assert not c.post("/api/wishlists/toggle/", {"product_id": str(product.pk)}, format="json").json()["wishlisted"]

    def test_sharing(self, user, other, product):
        c = client_for(user)
        wid = c.get("/api/wishlists/").json()[0]["id"]
        c.post(f"/api/wishlists/{wid}/add/", {"product_id": str(product.pk)}, format="json")
        token = c.post(f"/api/wishlists/{wid}/share/").json()["share_token"]
        public = client_for().get(f"/api/wishlists/shared/{token}/").json()
        assert len(public["items"]) == 1 and "share_token" not in public
        assert client_for(other).get(f"/api/wishlists/{wid}/").status_code == 404
        assert c.delete(f"/api/wishlists/{wid}/share/").status_code == 204
        assert client_for().get(f"/api/wishlists/shared/{token}/").status_code == 404

    def test_move_to_cart_requires_cart_app(self, user, product):
        c = client_for(user)
        wid = c.get("/api/wishlists/").json()[0]["id"]
        item = c.post(f"/api/wishlists/{wid}/add/", {"product_id": str(product.pk)}, format="json").json()
        resp = c.post(f"/api/wishlists/{wid}/items/{item['id']}/move-to-cart/")
        assert resp.status_code == 400 and resp.json()["error"] == "cart_not_installed"

    def test_price_drop_alerts(self, user, product):
        drops = []
        signals.wishlist_price_drop.connect(
            lambda sender, **kw: drops.append(kw["new_price"]), weak=False, dispatch_uid="t-drop"
        )
        try:
            services.add_to_wishlist(services.default_wishlist(user), product)
            Product.objects.filter(pk=product.pk).update(price=Decimal("980"))  # 2% — below threshold
            assert services.detect_price_drops() == {"alerts": 0}
            Product.objects.filter(pk=product.pk).update(price=Decimal("800"))
            assert services.detect_price_drops() == {"alerts": 1}
            assert services.detect_price_drops() == {"alerts": 0}  # only once
        finally:
            signals.wishlist_price_drop.disconnect(dispatch_uid="t-drop")
        assert drops == [Decimal("800.00")]

    def test_legacy_product_model_alias_and_str(self, user, product):
        c = client_for(user)
        wid = c.get("/api/wishlists/").json()[0]["id"]
        resp = c.post(
            f"/api/wishlists/{wid}/add/",
            {"product_id": str(product.pk), "product_model": "tests.Product"},
            format="json",
        )
        assert resp.status_code == 201
        assert "Wishlist(" in str(Wishlist.objects.get()) and "WishlistItem(" in str(WishlistItem.objects.get())


class TestQuestions:
    def test_ask_moderate_answer(self, user, other, staff, product):
        q = client_for(user).post(
            "/api/questions/",
            {"product_id": str(product.pk), "question": "Is it cotton?"},
            format="json",
        )
        assert q.status_code == 201 and q.json()["is_approved"] is False
        qid = q.json()["id"]
        assert client_for().get(f"/api/questions/?product_id={product.pk}").json()["count"] == 0
        assert client_for(staff).get("/api/questions/?status=pending").json()["count"] == 1
        assert client_for(other).post(f"/api/questions/{qid}/answers/", {"answer": "x"}).status_code == 404
        assert client_for(staff).post(f"/api/questions/{qid}/approve/").json() == {"approved": True}
        assert client_for(staff).post("/api/questions/00000000-0000-0000-0000-000000000000/approve/").status_code == 404
        a = client_for(staff).post(f"/api/questions/{qid}/answers/", {"answer": "Yes, 100% cotton"}).json()
        assert a["is_official"] is True
        client_for(other).post(f"/api/questions/{qid}/answers/", {"answer": "Mine was cotton too"})
        listing = client_for().get(f"/api/questions/?product_id={product.pk}").json()["results"][0]
        assert [ans["is_official"] for ans in listing["answers"]] == [True, False]
        assert client_for(other).delete(f"/api/questions/{qid}/").status_code == 404
        assert client_for(user).delete(f"/api/questions/{qid}/").status_code == 204
        assert str(ProductQuestion(question="Is it cotton?")) == "Is it cotton?"


class TestHistory:
    def test_recently_viewed_anonymous_then_login_merge(self, user, product):
        browser = APIClient()
        other_product = Product.objects.create()
        for p in (product, other_product, product):
            assert (
                browser.post("/api/recently-viewed/track/", {"product_id": str(p.pk)}, format="json").status_code == 201
            )
        from datetime import timedelta

        from django.utils import timezone

        RecentlyViewed.objects.filter(object_id=other_product.pk).update(
            updated_at=timezone.now() - timedelta(minutes=1)
        )
        items = browser.get("/api/recently-viewed/").json()
        assert [i["product_id"] for i in items] == [
            str(product.pk),
            str(other_product.pk),
        ]  # most recent first
        assert items[0]["view_count"] == 2
        assert browser.login(username="amaka", password="pass")
        mine = browser.get("/api/recently-viewed/").json()
        assert len(mine) == 2 and RecentlyViewed.objects.filter(user=user).count() == 2
        assert browser.delete("/api/recently-viewed/clear/").status_code == 204
        assert browser.get("/api/recently-viewed/").json() == []

    def test_limit(self, user, fc):
        fc(PRODUCT_MODELS=["tests.Product"], THROTTLING_ENABLED=False, RECENTLY_VIEWED_LIMIT=3)
        c = client_for(user)
        for _ in range(5):
            c.post(
                "/api/recently-viewed/track/",
                {"product_id": str(Product.objects.create().pk)},
                format="json",
            )
        assert len(c.get("/api/recently-viewed/").json()) == 3

    def test_searches_and_trending(self, user, fc):
        fc(PRODUCT_MODELS=["tests.Product"], THROTTLING_ENABLED=False, RECENT_SEARCHES_LIMIT=2)
        c = client_for(user)
        for q in ["iPhone 15", "iphone   15", "rice", "garri"]:
            assert c.post("/api/recent-searches/", {"query": q}, format="json").status_code == 201
        assert [s["query"] for s in c.get("/api/recent-searches/").json()] == ["garri", "rice"]
        trending = client_for().get("/api/recent-searches/trending/").json()
        assert trending[0] == {"term": "iphone 15", "count": 2}
        SearchTerm.objects.filter(term="rice").update(is_hidden=True)
        assert "rice" not in [t["term"] for t in client_for().get("/api/recent-searches/trending/").json()]
        assert c.delete("/api/recent-searches/clear/").status_code == 204
        assert RecentSearch.objects.count() == 0
        assert services.record_search(None, "   ") is None

    def test_anonymous_search_without_session_is_counted_only(self):
        from types import SimpleNamespace

        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
        assert services.record_search(request, "beans") is None
        assert SearchTerm.objects.get().term == "beans"

    def test_legacy_classmethods(self, user, product):
        from types import SimpleNamespace

        request = SimpleNamespace(user=user)
        RecentlyViewed.track(request, product)
        RecentSearch.record(request, "ankara")
        assert RecentlyViewed.objects.count() == 1 and RecentSearch.objects.count() == 1
        assert "ankara" in str(RecentSearch.objects.get()) and "RecentlyViewed(" in str(RecentlyViewed.objects.get())
        assert services.track_view(SimpleNamespace(user=SimpleNamespace(is_authenticated=False)), product) is None


class TestInfra:
    def test_engagement_models_setting(self, fc):
        fc(PRODUCT_MODELS=["tests.Product"], ENGAGEMENT_MODELS="tests.Product")
        assert services.engagement_model_paths() == ["tests.Product"]
        fc(PRODUCT_MODELS=[], ENGAGEMENT_MODELS=None)
        from flexcommerce_core.exceptions import InvalidProductTypeError

        with pytest.raises(InvalidProductTypeError):
            services.resolve_product(None, uuid.uuid4())

    def test_migrations_and_admin(self, client, user, product):
        from django.contrib.auth import get_user_model

        call_command("makemigrations", "flexcommerce_engagement", "--check", "--dry-run", stdout=StringIO())
        services.add_to_wishlist(services.default_wishlist(user), product)
        ProductQuestion.objects.create(
            content_type_id=services.product_ref(product)[0].pk,
            object_id=product.pk,
            user=user,
            question="q?",
        )
        SearchTerm.objects.create(term="x", count=1)
        client.force_login(get_user_model().objects.create_superuser("root", "r@x.com", "x"))
        for m in ("wishlist", "productreview", "productquestion", "searchterm"):
            assert client.get(f"/admin/flexcommerce_engagement/{m}/").status_code == 200
        assert str(SearchTerm.objects.get()) == "x (1)"
