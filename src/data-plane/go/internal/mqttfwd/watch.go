package mqttfwd

import (
	"context"
	"time"

	"github.com/eclipse/paho.golang/autopaho"
	"github.com/eclipse/paho.golang/paho"
)

// Watch adds a reference to an extra topic filter (the console's live MQTT
// view) and subscribes on the first one. Messages on it are forwarded like the
// device topics. The subscription belongs to this forwarder only.
func (s *Session) Watch(ctx context.Context, topic string) error {
	s.opMu.Lock()
	defer s.opMu.Unlock()
	s.mu.Lock()
	cm, up := s.cm, s.up
	if cm == nil {
		s.mu.Unlock()
		return ErrNotRunning
	}
	s.watches[topic]++
	first := s.watches[topic] == 1
	s.mu.Unlock()
	if !first || s.skipWatch(topic) || !up {
		return nil // covered by the device topics, or not connected: subscribeAll picks it up
	}
	sctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := cm.Subscribe(sctx, s.watchSubscribe([]string{topic})); err != nil {
		// Same as the Python service: logged, not returned; a reconnect retries it.
		s.log.Warn("MQTT platform subscribe request failed", "topic", topic, "error", err.Error())
	}
	return nil
}

// Unwatch drops a reference and unsubscribes on the last one.
func (s *Session) Unwatch(ctx context.Context, topic string) error {
	s.opMu.Lock()
	defer s.opMu.Unlock()
	s.mu.Lock()
	cm, up := s.cm, s.up
	if cm == nil {
		s.mu.Unlock()
		return ErrNotRunning
	}
	last := s.watches[topic] <= 1
	if last {
		delete(s.watches, topic)
	} else {
		s.watches[topic]--
	}
	s.mu.Unlock()
	if !last || s.skipWatch(topic) || !up {
		return nil
	}
	uctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := cm.Unsubscribe(uctx, &paho.Unsubscribe{Topics: []string{topic}}); err != nil {
		s.log.Warn("MQTT platform unsubscribe failed", "topic", topic, "error", err.Error())
	}
	return nil
}

// deviceSubscribe is the subscription to the device topics.
func (s *Session) deviceSubscribe() *paho.Subscribe {
	var subs []paho.SubscribeOptions
	for _, t := range s.sharedTopics() {
		subs = append(subs, paho.SubscribeOptions{Topic: t, QoS: 1})
	}
	return &paho.Subscribe{Subscriptions: subs}
}

// watchSubscribe subscribes to watched filters, marked with watchSubID when
// overlapping copies can be told apart (see overlap.go).
func (s *Session) watchSubscribe(topics []string) *paho.Subscribe {
	sub := &paho.Subscribe{}
	for _, t := range topics {
		sub.Subscriptions = append(sub.Subscriptions, paho.SubscribeOptions{Topic: t, QoS: 1})
	}
	if s.dedupWatches() {
		id := watchSubID
		sub.Properties = &paho.SubscribeProperties{SubscriptionIdentifier: &id}
	}
	return sub
}

// skipWatch reports whether a watched filter needs no subscription of its own:
// the device topics cover it and its copies could not be told apart.
func (s *Session) skipWatch(topic string) bool {
	return coveredByDefault(topic) && !s.dedupWatches()
}

// dedupWatches reports whether watch copies of device topics can be dropped:
// the broker supports subscription identifiers and device topics are shared.
func (s *Session) dedupWatches() bool {
	return s.cfg.SharedGroup != "" && s.subIDs.Load()
}

// watchTopics are the watched filters that need their own subscription (those
// the device topics do not already cover).
func (s *Session) watchTopics() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []string
	for t := range s.watches {
		if !s.skipWatch(t) {
			out = append(out, t)
		}
	}
	return out
}

// dropWatches removes the watch subscriptions (and a retained replay still in
// progress) on shutdown, so the session the broker keeps holds only the device
// topics: watches are this process's state.
func (s *Session) dropWatches(cm *autopaho.ConnectionManager) {
	s.opMu.Lock()
	defer s.opMu.Unlock()
	topics := s.watchTopics()
	if s.cfg.SharedGroup != "" {
		topics = append(topics, retainedTopic)
	}
	if len(topics) == 0 {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := cm.Unsubscribe(ctx, &paho.Unsubscribe{Topics: topics}); err != nil {
		s.log.Debug("MQTT unsubscribe failed", "error", err.Error())
	}
}
