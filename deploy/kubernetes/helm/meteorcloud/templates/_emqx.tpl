{{/* Same broker setup as deploy/compose/config/emqx/emqx.conf: TLS only, and
     every login and publish/subscribe is checked by the API. */}}
{{- define "meteorcloud.emqxConf" -}}
node {
  name = "emqx@127.0.0.1"
  data_dir = "/opt/emqx/data"
}
listeners.tcp.default { enable = false }
listeners.ws.default { enable = false }
listeners.wss.default { enable = false }
listeners.ssl.default {
  bind = "0.0.0.0:8883"
  ssl_options {
    cacertfile = "/opt/emqx/etc/certs/ca.crt"
    certfile = "/opt/emqx/etc/certs/server.crt"
    keyfile = "/opt/emqx/etc/certs/server.key"
    verify = verify_none
    fail_if_no_peer_cert = false
  }
}
authentication = [
  {
    mechanism = password_based
    backend = http
    enable = true
    method = post
    url = "{{ printf "http://%s-backend:8000" (include "meteorcloud.fullname" .) }}/internal/mqtt/authenticate"
    headers { content-type = "application/json" }
    body { username = "${username}", password = "${password}", clientid = "${clientid}" }
    ssl.enable = false
    connect_timeout = "5s"
    request_timeout = "5s"
  }
  {{- if and .Values.dataApi.enabled .Values.dataApi.mqtt.direct }}
  ,
  # Application identities (username "app_<credential id>"): the API answers "ignore"
  # and EMQX checks the short-lived token the data API signed (signature, username,
  # expiry, and the topic ACL inside it). The secret arrives as
  # EMQX_AUTHENTICATION__2__SECRET from the release Secret.
  {
    mechanism = jwt
    enable = true
    from = password
    use_jwks = false
    algorithm = "hmac-based"
    secret = "unset-unset-unset-unset-unset-unset"
    secret_base64_encoded = false
    verify_claims { username = "${username}" }
    acl_claim_name = "acl"
    disconnect_after_expire = true
  }
  {{- end }}
]
authorization {
  no_match = deny
  deny_action = disconnect
  cache { enable = false }
  sources = [
    {
      type = http
      enable = true
      method = post
      url = "{{ printf "http://%s-backend:8000" (include "meteorcloud.fullname" .) }}/internal/mqtt/authorize"
      headers { content-type = "application/json" }
      body { username = "${username}", clientid = "${clientid}", topic = "${topic}", action = "${action}", retain = "${retain}" }
    }
  ]
}
dashboard { listeners.http { bind = 18083 } }
{{- end }}
