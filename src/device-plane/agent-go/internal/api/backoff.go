package api

import (
	"errors"
	"math/rand/v2"
	"time"
)

// Backoff spaces out retries of a server call: Min doubling to Max, each delay
// multiplied by a random factor in [0.5, 1.0) so a fleet does not retry in step.
// A Retry-After from the server wins when it is longer. Not safe for concurrent
// use: each loop owns its Backoff.
type Backoff struct {
	Min, Max time.Duration
	// Rand returns a number in [0, 1); nil means math/rand/v2.
	Rand func() float64

	next time.Duration
}

// NewBackoff returns the contract default for heartbeats: 1 s doubling to 60 s.
func NewBackoff() *Backoff { return &Backoff{Min: time.Second, Max: time.Minute} }

// Next returns how long to wait before the next try after err.
func (b *Backoff) Next(err error) time.Duration {
	if b.next < b.Min {
		b.next = b.Min
	}
	r := b.Rand
	if r == nil {
		r = rand.Float64
	}
	d := time.Duration(float64(b.next) * (0.5 + r()/2))
	b.next = min(b.next*2, b.Max)

	var e *Error
	if errors.As(err, &e) && e.RetryAfter > d {
		d = e.RetryAfter
	}
	return d
}

// Reset starts over at Min after a success.
func (b *Backoff) Reset() { b.next = 0 }
