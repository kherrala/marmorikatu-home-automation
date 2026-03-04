#!/bin/sh
# Copy the nginx config template (no variable substitution needed).
cp /etc/nginx/templates/default.conf.template /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
