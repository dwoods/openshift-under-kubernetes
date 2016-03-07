FROM alpine:3.3
WORKDIR /root
RUN apk update && apk add openssl openssl-dev
RUN echo "cat /var/run/secrets/kubernetes.io/serviceaccount/ca.crt | openssl x509 -pubkey -noout" > get_key.sh && chmod +x get_key.sh
CMD ["sh", "/root/get_key.sh"]
