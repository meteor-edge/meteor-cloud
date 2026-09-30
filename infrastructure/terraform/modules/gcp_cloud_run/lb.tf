resource "google_compute_global_address" "lb" {
  name = "${var.installation_name}-lb"
}

resource "google_compute_region_network_endpoint_group" "backend" {
  name                  = "${var.installation_name}-backend-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region
  cloud_run {
    service = google_cloud_run_v2_service.backend.name
  }
}

resource "google_compute_region_network_endpoint_group" "frontend" {
  name                  = "${var.installation_name}-frontend-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region
  cloud_run {
    service = google_cloud_run_v2_service.frontend.name
  }
}

resource "google_compute_backend_service" "backend" {
  name                  = "${var.installation_name}-backend"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.backend.id
  }
}

resource "google_compute_backend_service" "frontend" {
  name                  = "${var.installation_name}-frontend"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.frontend.id
  }
}

resource "google_compute_url_map" "app" {
  name            = "${var.installation_name}-url-map"
  default_service = google_compute_backend_service.frontend.id

  host_rule {
    hosts        = ["*"]
    path_matcher = "app"
  }

  path_matcher {
    name            = "app"
    default_service = google_compute_backend_service.frontend.id

    path_rule {
      paths = [
        "/api",
        "/api/*",
        "/health",
        "/docs",
        "/docs/*",
        "/redoc",
        "/redoc/*",
        "/openapi.json",
        "/metrics",
        "/internal",
        "/internal/*",
      ]
      service = google_compute_backend_service.backend.id
    }
  }
}

resource "google_compute_target_http_proxy" "app" {
  name    = "${var.installation_name}-http"
  url_map = local.use_https ? google_compute_url_map.https_redirect[0].id : google_compute_url_map.app.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${var.installation_name}-http"
  ip_address            = google_compute_global_address.lb.address
  port_range            = "80"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_http_proxy.app.id
}

resource "google_compute_url_map" "https_redirect" {
  count = local.use_https ? 1 : 0
  name  = "${var.installation_name}-https-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_managed_ssl_certificate" "app" {
  count = local.use_https ? 1 : 0
  name  = "${var.installation_name}-cert"

  managed {
    domains = [var.domain]
  }
}

resource "google_compute_target_https_proxy" "app" {
  count            = local.use_https ? 1 : 0
  name             = "${var.installation_name}-https"
  url_map          = google_compute_url_map.app.id
  ssl_certificates = [google_compute_managed_ssl_certificate.app[0].id]
}

resource "google_compute_global_forwarding_rule" "https" {
  count                 = local.use_https ? 1 : 0
  name                  = "${var.installation_name}-https"
  ip_address            = google_compute_global_address.lb.address
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_https_proxy.app[0].id
}
