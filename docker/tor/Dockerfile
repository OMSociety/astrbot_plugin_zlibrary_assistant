FROM alpine:3.22

RUN apk add --no-cache tor

COPY torrc /etc/tor/torrc
COPY entrypoint.sh /usr/local/bin/tor-entrypoint
RUN chmod 755 /usr/local/bin/tor-entrypoint

USER tor
EXPOSE 9050
ENTRYPOINT ["/usr/local/bin/tor-entrypoint"]
