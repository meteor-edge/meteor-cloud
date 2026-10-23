# Minimal app for the MeteorCloud E2E suite: reports which run, device and version served the request.
FROM busybox:1.36.1
COPY serve.sh /serve.sh
RUN chmod 0755 /serve.sh
USER 65534
EXPOSE 8080
CMD ["/serve.sh"]
