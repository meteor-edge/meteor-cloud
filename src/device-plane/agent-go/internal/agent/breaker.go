package agent

import "time"

// Breaker counts failures in a sliding window. When an optional module fails
// Limit times within Window it is parked, so a broken feature stops burning CPU
// and filling the journal while the rest of the agent keeps working.
type Breaker struct {
	Limit  int
	Window time.Duration

	failures []time.Time
}

// Record adds a failure at now and reports whether the breaker tripped.
func (b *Breaker) Record(now time.Time) bool {
	cutoff := now.Add(-b.Window)
	kept := b.failures[:0]
	for _, t := range b.failures {
		if t.After(cutoff) {
			kept = append(kept, t)
		}
	}
	b.failures = append(kept, now)
	return len(b.failures) >= b.Limit
}

// Reset forgets past failures (after an unpark or a long healthy run).
func (b *Breaker) Reset() { b.failures = b.failures[:0] }

// restartDelay is the backoff between restarts: min doubling to max.
type restartDelay struct {
	min, max time.Duration
	next     time.Duration
}

// take returns the delay to wait now and doubles the next one.
func (r *restartDelay) take() time.Duration {
	if r.next < r.min {
		r.next = r.min
	}
	d := r.next
	r.next = min(r.next*2, r.max)
	return d
}

func (r *restartDelay) reset() { r.next = r.min }
