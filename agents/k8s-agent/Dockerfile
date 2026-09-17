FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -ldflags "-s -w" -o /cronsentinel-k8s ./cmd/cronsentinel-k8s
FROM gcr.io/distroless/static:nonroot
COPY --from=build /cronsentinel-k8s /cronsentinel-k8s
USER nonroot
ENTRYPOINT ["/cronsentinel-k8s"]
