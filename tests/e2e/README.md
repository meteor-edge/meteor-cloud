# MeteorCloud full-stack E2E

```bash
cp .env.example .env        # E2E_ENV_NAME, E2E_DNS_ZONE_ID, E2E_DOMAIN, ...
./meteorcloud-testenv validate
./meteorcloud-testenv up        # provision + configure + 55 acceptance checks
./demo.sh up                    # demo setup to explore by hand (up --no-tests + seed)
./meteorcloud-testenv status    # hosts, SSH commands, URLs, DNS records, last result
./meteorcloud-testenv test      # re-run the suite on the same environment
./meteorcloud-testenv logs      # redacted diagnostics into .state/<env>/logs/
./meteorcloud-testenv down      # destroy only this environment's resources
```

Layout: `e2e/` orchestrator (stdlib only), `e2e/suite/` acceptance checks,
`terraform/aws` and `terraform/onprem` roots, `testapp/` the deployed app,
`tests/` unit tests (`make e2e-check`). State, keys, generated secrets and
reports are in `.state/<env>/` (gitignored).

DNS: `<env>.<domain>`, `*.<env>.<domain>` and `wg.<env>.<domain>`. Route53 mode
creates them; `E2E_DNS_PROVIDER=manual` prints them for you to create
([docs/dns.md](../../docs/dns.md)).

Full guide: [docs/e2e.md](../../docs/e2e.md).
