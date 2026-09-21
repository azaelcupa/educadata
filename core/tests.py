from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.views import build_national_indicators, get_national_indicators


class BuildNationalIndicatorsTests(SimpleTestCase):
    def test_uses_enrollment_weighted_average_when_weights_exist(self):
        indicator_docs = [
            {"entidad_clave": "1", "cobertura": 0.5, "tasa_absorcion": 1.0},
            {"entidad_clave": "2", "cobertura": 0.9, "tasa_absorcion": 1.2},
        ]

        national = build_national_indicators(indicator_docs, {"1": 100, "2": 900})

        self.assertEqual(national["cobertura"], 86.0)
        self.assertEqual(national["absorcion"], 118.0)

    def test_falls_back_to_simple_average_when_no_weights_exist(self):
        indicator_docs = [
            {"entidad_clave": "1", "cobertura": 0.5},
            {"entidad_clave": "2", "cobertura": 0.9},
        ]

        national = build_national_indicators(indicator_docs, {})

        self.assertEqual(national["cobertura"], 70.0)

    def test_uses_persisted_national_document_when_available(self):
        class StubCollection:
            def find_one(self, *_args, **_kwargs):
                return {"cobertura": 0.806, "tasa_absorcion": 1.024}

        class StubDb:
            indicadores_nacionales_ciclo = StubCollection()

        national = get_national_indicators(
            StubDb(),
            "2024-2025",
            [{"entidad_clave": "1", "cobertura": 0.5}],
            {"1": 100},
        )

        self.assertEqual(national["cobertura"], 80.6)
        self.assertEqual(national["absorcion"], 102.4)

    def test_falls_back_to_computed_national_when_collection_has_no_cycle(self):
        class StubCollection:
            def find_one(self, *_args, **_kwargs):
                return None

        class StubDb:
            indicadores_nacionales_ciclo = StubCollection()

        national = get_national_indicators(
            StubDb(),
            "2024-2025",
            [
                {"entidad_clave": "1", "cobertura": 0.5},
                {"entidad_clave": "2", "cobertura": 0.9},
            ],
            {},
        )

        self.assertEqual(national["cobertura"], 70.0)


class AuthenticationFlowTests(TestCase):
    @override_settings(AUTH_DISABLED=False)
    def test_login_page_is_available(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Iniciar sesi")

    @override_settings(AUTH_DISABLED=False)
    def test_home_requires_authentication(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    @override_settings(AUTH_DISABLED=False)
    def test_valid_credentials_redirect_to_home(self):
        user = get_user_model().objects.create_user(username="demo", password="ClaveSegura123")

        response = self.client.post(
            reverse("login"),
            {"username": user.username, "password": "ClaveSegura123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("home"))

    @override_settings(AUTH_DISABLED=True)
    def test_login_route_redirects_to_home_when_auth_is_disabled(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("home"))
