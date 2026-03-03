#!/bin/sh
# Substitute environment variables into the nginx config template.
# Only replace the variables we control — leave nginx's own $uri etc. intact.
envsubst '${OLLAMA_URL} ${OLLAMA_MODEL} ${OPENAI_API_KEY} ${ANTHROPIC_API_KEY}' \
  < /etc/nginx/templates/default.conf.template \
  > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
