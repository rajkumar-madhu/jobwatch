package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"

	"github.com/cronsentinel/k8s-agent/internal/watcher"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func main() {
	server := env("CS_SERVER", "")
	if server == "" {
		log.Fatal("CS_SERVER required")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()

	agentID, agentKey := env("CS_AGENT_ID", ""), env("CS_AGENT_KEY", "")
	if agentKey == "" { // first boot: enroll with bootstrap token and persist to the mounted secret path
		id, key, err := watcher.Enroll(ctx, server, env("CS_BOOTSTRAP_TOKEN", ""), env("CS_CLUSTER_NAME", "cluster"), env("CS_INSECURE", "false"))
		if err != nil {
			log.Fatalf("enroll: %v", err)
		}
		agentID, agentKey = id, key
		_ = os.WriteFile(env("CS_KEY_FILE", "/var/lib/cronsentinel/agent.key"), []byte(id+"\n"+key), 0o600)
		log.Println("enrolled as", id, "— persist CS_AGENT_ID/CS_AGENT_KEY in the Secret for restarts")
	}
	cfg, err := rest.InClusterConfig()
	if err != nil {
		log.Fatalf("in-cluster config: %v", err)
	}
	cs, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		log.Fatal(err)
	}
	var ns []string
	if v := env("CS_NAMESPACES", ""); v != "" {
		ns = strings.Split(v, ",")
	}
	client := watcher.NewClient(server, agentID, agentKey, env("CS_INSECURE", "false") == "true")
	log.Printf("cronsentinel-k8s %s starting (scope=%v)", watcher.Version, ns)
	watcher.New(cs, client, ns).Run(ctx, 2*time.Minute, 5*time.Second)
}
