// Package watcher uses client-go informers on CronJobs, Jobs, Pods and Events and translates them into
// WeCrew JobWatch discovery + execution events. Read-only; RBAC in the Helm chart grants get/list/watch only.
package watcher

import (
	"context"
	"log"
	"strings"
	"sync"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"
)

// Failure reasons we detect explicitly (spec §8).
var podFailureReasons = map[string]bool{"ImagePullBackOff": true, "ErrImagePull": true, "CrashLoopBackOff": true, "OOMKilled": true, "Evicted": true, "DeadlineExceeded": true, "CreateContainerConfigError": true}

type CronJobSpec struct {
	Namespace, Name, Schedule, ConcurrencyPolicy, Image, Command string
	Suspend                                                     bool
	SuccessfulHistoryLimit, FailedHistoryLimit                   *int32
	ActiveDeadlineS, StartingDeadlineS                           *int64
	LastScheduleAt, LastSuccessAt                                *time.Time
	UID                                                          string
}

type ExecEvent struct {
	Kind        string            `json:"kind"` // start|success|fail
	CronJobUID  string            `json:"cronjob_uid"`
	Namespace   string            `json:"namespace"`
	CronJob     string            `json:"cronjob"`
	JobName     string            `json:"job_name"`
	ExecutionID string            `json:"execution_id"`
	Sequence    int               `json:"sequence"`
	AgentTS     time.Time         `json:"agent_ts"`
	DurationMS  *int64            `json:"duration_ms,omitempty"`
	ExitCode    *int              `json:"exit_code,omitempty"`
	Pod, Node   string            `json:"pod,omitempty"`
	Reason      string            `json:"reason,omitempty"`
	StderrTail  string            `json:"stderr_tail,omitempty"`
	Meta        map[string]string `json:"meta,omitempty"`
}

type Watcher struct {
	cs        kubernetes.Interface
	client    *Client
	nsFilter  map[string]bool // empty = cluster scope
	mu        sync.Mutex
	pending   []ExecEvent
	jobToCron map[string]string // job uid -> cronjob uid
	started   map[string]bool
}

func New(cs kubernetes.Interface, client *Client, namespaces []string) *Watcher {
	w := &Watcher{cs: cs, client: client, nsFilter: map[string]bool{}, jobToCron: map[string]string{}, started: map[string]bool{}}
	for _, n := range namespaces {
		if n != "" {
			w.nsFilter[n] = true
		}
	}
	return w
}

func (w *Watcher) inScope(ns string) bool { return len(w.nsFilter) == 0 || w.nsFilter[ns] }

func (w *Watcher) push(e ExecEvent) {
	w.mu.Lock()
	w.pending = append(w.pending, e)
	w.mu.Unlock()
}

func cronJobSpec(cj *batchv1.CronJob) CronJobSpec {
	s := CronJobSpec{Namespace: cj.Namespace, Name: cj.Name, Schedule: cj.Spec.Schedule, ConcurrencyPolicy: string(cj.Spec.ConcurrencyPolicy), UID: string(cj.UID),
		SuccessfulHistoryLimit: cj.Spec.SuccessfulJobsHistoryLimit, FailedHistoryLimit: cj.Spec.FailedJobsHistoryLimit, StartingDeadlineS: cj.Spec.StartingDeadlineSeconds,
		ActiveDeadlineS: cj.Spec.JobTemplate.Spec.ActiveDeadlineSeconds}
	if cj.Spec.Suspend != nil {
		s.Suspend = *cj.Spec.Suspend
	}
	if cj.Status.LastScheduleTime != nil {
		t := cj.Status.LastScheduleTime.Time
		s.LastScheduleAt = &t
	}
	if cj.Status.LastSuccessfulTime != nil {
		t := cj.Status.LastSuccessfulTime.Time
		s.LastSuccessAt = &t
	}
	if cs := cj.Spec.JobTemplate.Spec.Template.Spec.Containers; len(cs) > 0 {
		s.Image = cs[0].Image
		s.Command = strings.Join(append(cs[0].Command, cs[0].Args...), " ")
	}
	return s
}

// Run starts informers and the flush loop; blocks until ctx is done.
func (w *Watcher) Run(ctx context.Context, discoveryEvery, flushEvery time.Duration) {
	f := informers.NewSharedInformerFactory(w.cs, 10*time.Minute)
	cjInf := f.Batch().V1().CronJobs().Informer()
	jobInf := f.Batch().V1().Jobs().Informer()
	podInf := f.Core().V1().Pods().Informer()
	evInf := f.Core().V1().Events().Informer()

	jobInf.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc:    func(o any) { w.onJob(o.(*batchv1.Job)) },
		UpdateFunc: func(_, o any) { w.onJob(o.(*batchv1.Job)) },
	})
	podInf.AddEventHandler(cache.ResourceEventHandlerFuncs{
		UpdateFunc: func(_, o any) { w.onPod(o.(*corev1.Pod)) },
	})
	evInf.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: func(o any) { w.onEvent(o.(*corev1.Event)) },
	})
	f.Start(ctx.Done())
	f.WaitForCacheSync(ctx.Done())
	log.Println("informers synced")

	go func() { // discovery of CronJob specs
		for {
			var specs []CronJobSpec
			for _, o := range cjInf.GetStore().List() {
				cj := o.(*batchv1.CronJob)
				if w.inScope(cj.Namespace) {
					specs = append(specs, cronJobSpec(cj))
				}
			}
			if err := w.client.Post(ctx, "/agent/v1/k8s/cronjobs", map[string]any{"cronjobs": specs}, nil); err != nil {
				log.Printf("discovery: %v", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(discoveryEvery):
			}
		}
	}()
	for { // flush
		select {
		case <-ctx.Done():
			return
		case <-time.After(flushEvery):
		}
		w.mu.Lock()
		batch := w.pending
		w.pending = nil
		w.mu.Unlock()
		if len(batch) == 0 {
			continue
		}
		if err := w.client.Post(ctx, "/agent/v1/k8s/events", map[string]any{"events": batch}, nil); err != nil {
			log.Printf("flush: %v (re-queue %d)", err, len(batch))
			w.mu.Lock()
			w.pending = append(batch, w.pending...) // TODO: disk buffer like linux-agent
			w.mu.Unlock()
		}
	}
}

func (w *Watcher) onJob(j *batchv1.Job) {
	if !w.inScope(j.Namespace) {
		return
	}
	var cronUID, cronName string
	for _, o := range j.OwnerReferences {
		if o.Kind == "CronJob" {
			cronUID, cronName = string(o.UID), o.Name
		}
	}
	if cronUID == "" {
		return // standalone Job — TODO optional tracking
	}
	w.mu.Lock()
	w.jobToCron[string(j.UID)] = cronUID
	seen := w.started[string(j.UID)]
	w.mu.Unlock()
	base := ExecEvent{CronJobUID: cronUID, Namespace: j.Namespace, CronJob: cronName, JobName: j.Name, ExecutionID: "k8s-" + string(j.UID), AgentTS: time.Now().UTC(), Meta: map[string]string{}}
	if !seen && j.Status.StartTime != nil {
		w.mu.Lock()
		w.started[string(j.UID)] = true
		w.mu.Unlock()
		e := base
		e.Kind, e.Sequence, e.AgentTS = "start", 0, j.Status.StartTime.Time
		w.push(e)
	}
	if j.Status.CompletionTime != nil && j.Status.Succeeded > 0 {
		e := base
		e.Kind, e.Sequence, e.AgentTS = "success", 1, j.Status.CompletionTime.Time
		code := 0
		e.ExitCode = &code
		if j.Status.StartTime != nil {
			d := j.Status.CompletionTime.Sub(j.Status.StartTime.Time).Milliseconds()
			e.DurationMS = &d
		}
		w.push(e)
		return
	}
	for _, c := range j.Status.Conditions {
		if c.Type == batchv1.JobFailed && c.Status == corev1.ConditionTrue {
			e := base
			e.Kind, e.Sequence, e.Reason = "fail", 1, c.Reason
			code := 1
			e.ExitCode = &code
			e.StderrTail = c.Reason + ": " + c.Message
			if j.Status.StartTime != nil {
				d := time.Since(j.Status.StartTime.Time).Milliseconds()
				e.DurationMS = &d
			}
			w.push(e)
		}
	}
}

// onPod enriches with node/pod + explicit failure reasons (OOMKilled etc.) for pods owned by tracked Jobs.
func (w *Watcher) onPod(p *corev1.Pod) {
	if !w.inScope(p.Namespace) {
		return
	}
	var jobUID string
	for _, o := range p.OwnerReferences {
		if o.Kind == "Job" {
			jobUID = string(o.UID)
		}
	}
	w.mu.Lock()
	cronUID, ok := w.jobToCron[jobUID]
	w.mu.Unlock()
	if !ok {
		return
	}
	for _, cs := range p.Status.ContainerStatuses {
		reason := ""
		if cs.State.Waiting != nil {
			reason = cs.State.Waiting.Reason
		}
		if cs.State.Terminated != nil && cs.State.Terminated.ExitCode != 0 {
			reason = cs.State.Terminated.Reason
		}
		if cs.LastTerminationState.Terminated != nil && cs.LastTerminationState.Terminated.Reason == "OOMKilled" {
			reason = "OOMKilled"
		}
		if podFailureReasons[reason] || p.Status.Reason == "Evicted" {
			if p.Status.Reason == "Evicted" {
				reason = "Evicted"
			}
			w.push(ExecEvent{Kind: "progress", CronJobUID: cronUID, Namespace: p.Namespace, JobName: p.Labels["job-name"], ExecutionID: "k8s-" + jobUID, Sequence: 5,
				AgentTS: time.Now().UTC(), Pod: p.Name, Node: p.Spec.NodeName, Reason: reason, Meta: map[string]string{"pod_phase": string(p.Status.Phase)}})
		}
	}
}

// onEvent forwards warning events (FailedScheduling, node pressure, FailedMount/PVC, DNS) attached to tracked pods/jobs.
func (w *Watcher) onEvent(ev *corev1.Event) {
	if ev.Type != corev1.EventTypeWarning || !w.inScope(ev.Namespace) || time.Since(ev.LastTimestamp.Time) > 2*time.Minute {
		return
	}
	interesting := map[string]bool{"FailedScheduling": true, "FailedMount": true, "FailedAttachVolume": true, "NodeNotReady": true, "Evicted": true, "BackOff": true, "DNSConfigForming": true, "FailedCreatePodSandBox": true}
	if !interesting[ev.Reason] {
		return
	}
	w.push(ExecEvent{Kind: "k8s_event", Namespace: ev.Namespace, JobName: ev.InvolvedObject.Name, AgentTS: ev.LastTimestamp.Time, Reason: ev.Reason,
		StderrTail: ev.Message, Meta: map[string]string{"object_kind": ev.InvolvedObject.Kind, "count": string(rune(ev.Count))}})
}
