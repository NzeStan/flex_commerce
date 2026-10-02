from rest_framework.routers import DefaultRouter

from .views import BrandViewSet, CategoryViewSet, ProductImageViewSet, ProductViewSet, VariantViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("catalog/categories", CategoryViewSet, basename="catalog-category")
router.register("catalog/brands", BrandViewSet, basename="catalog-brand")
router.register("catalog/products", ProductViewSet, basename="catalog-product")
router.register("catalog/variants", VariantViewSet, basename="catalog-variant")
router.register("catalog/images", ProductImageViewSet, basename="catalog-image")

urlpatterns = router.urls
