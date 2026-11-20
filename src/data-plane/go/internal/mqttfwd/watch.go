package mqttfwd

import (
	"context"
	"slices"
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
	if !first || slices.Contains(DefaultTopics, topic) || !up {
		return nil // not connected: subscribeAll picks it up on connect
	}
	sctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := cm.Subscribe(sctx, &paho.Subscribe{Subscriptions: []paho.SubscribeOptions{{Topic: topic, QoS: 1}}}); err != nil {
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
	if !last || slices.Contains(DefaultTopics, topic) || !up {
		return nil
	}
	uctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := cm.Unsubscribe(uctx, &paho.Unsubscribe{Topics: []string{topic}}); err != nil {
		s.log.Warn("MQTT platform unsubscribe failed", "topic", topic, "error", err.Error())
	}
	return nil
}

// subscriptions is everything to subscribe to after (re)connecting.
func (s *Session) subscriptions() []paho.SubscribeOptions {
	var subs []paho.SubscribeOptions
	for _, t := range s.sharedTopics() {
		subs = append(subs, paho.SubscribeOptions{Topic: t, QoS: 1})
	}
	for _, t := range s.watchTopics() {
		subs = append(subs, paho.SubscribeOptions{Topic: t, QoS: 1})
	}
	return subs
}

// watchTopics are the watched filters that need their own subscription.
func (s *Session) watchTopics() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []string
	for t := range s.watches {
		if !slices.Contains(DefaultTopics, t) {
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
