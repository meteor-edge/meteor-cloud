package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func sum(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }

func TestDownload(t *testing.T) {
	body := []byte(strings.Repeat("artifact bytes ", 10000))
	good := sum(body)
	tests := []struct {
		name     string
		api      func(w http.ResponseWriter, r *http.Request, storage string)
		wantCode string
	}{
		{name: "bytes with checksum", api: func(w http.ResponseWriter, r *http.Request, _ string) {
			w.Header().Set(checksumHeader, good)
			_, _ = w.Write(body)
		}},
		{name: "bytes without checksum", api: func(w http.ResponseWriter, r *http.Request, _ string) { _, _ = w.Write(body) }},
		{name: "wrong checksum", api: func(w http.ResponseWriter, r *http.Request, _ string) {
			w.Header().Set(checksumHeader, sum([]byte("other")))
			_, _ = w.Write(body)
		}, wantCode: "checksum_mismatch"},
		{name: "redirect to storage", api: func(w http.ResponseWriter, r *http.Request, storage string) {
			w.Header().Set("Location", storage+"/blob?X-Amz-Signature=abc")
			w.Header().Set(checksumHeader, good)
			w.WriteHeader(http.StatusFound)
		}},
		{name: "relative redirect", api: func(w http.ResponseWriter, r *http.Request, _ string) {
			w.Header().Set("Location", "/storage/blob")
			w.Header().Set(checksumHeader, good)
			w.WriteHeader(http.StatusTemporaryRedirect)
		}},
		{name: "redirect without checksum", api: func(w http.ResponseWriter, r *http.Request, storage string) {
			w.Header().Set("Location", storage+"/blob")
			w.WriteHeader(http.StatusFound)
		}, wantCode: "checksum_missing"},
		{name: "redirect without location", api: func(w http.ResponseWriter, r *http.Request, _ string) {
			w.Header().Set(checksumHeader, good)
			w.WriteHeader(http.StatusFound)
		}, wantCode: "download_redirect_invalid"},
		{name: "redirect to another scheme", api: func(w http.ResponseWriter, r *http.Request, _ string) {
			w.Header().Set("Location", "file:///etc/shadow")
			w.Header().Set(checksumHeader, good)
			w.WriteHeader(http.StatusFound)
		}, wantCode: "download_redirect_insecure"},
		{name: "not found", api: func(w http.ResponseWriter, r *http.Request, _ string) {
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"error":{"code":"artifact_not_found","message":"x"}}`))
		}, wantCode: "artifact_not_found"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var storageAuth, storageUA []string
			storage := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				storageAuth, storageUA = r.Header.Values("Authorization"), r.Header.Values("User-Agent")
				_, _ = w.Write(body)
			}))
			defer storage.Close()
			var apiHeaders http.Header
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/storage/blob" {
					storageAuth = r.Header.Values("Authorization")
					_, _ = w.Write(body)
					return
				}
				apiHeaders = r.Header.Clone()
				tc.api(w, r, storage.URL)
			}))
			defer srv.Close()
			c, _ := New(Options{BaseURL: srv.URL, Version: "1.2.3", HTTPClient: srv.Client()})
			var out bytes.Buffer
			var last int64
			got, err := c.Download(context.Background(), "dev_x", "0f9a-11", &out, func(n, _ int64) { last = n })
			if tc.wantCode != "" {
				var e *Error
				if !errors.As(err, &e) || e.Code != tc.wantCode {
					t.Fatalf("err = %v, want code %s", err, tc.wantCode)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got != good || !bytes.Equal(out.Bytes(), body) || last != int64(len(body)) {
				t.Fatalf("checksum %s, %d bytes, progress %d", got, out.Len(), last)
			}
			if apiHeaders.Get("Authorization") != "Bearer dev_x" || apiHeaders.Get(redirectHeader) != "1" ||
				apiHeaders.Get("User-Agent") != "meteorcli/1.2.3" || apiHeaders.Get(ProtocolHeader) != "1" {
				t.Errorf("api headers %v", apiHeaders)
			}
			if len(storageAuth) != 0 || len(storageUA) != 0 {
				t.Errorf("storage got Authorization %v, User-Agent %v", storageAuth, storageUA)
			}
		})
	}
}

func TestDownloadRefusesHTTPAfterHTTPS(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", "http://storage.example/blob")
		w.Header().Set(checksumHeader, strings.Repeat("a", 64))
		w.WriteHeader(http.StatusFound)
	}))
	defer srv.Close()
	c, _ := New(Options{BaseURL: srv.URL, HTTPClient: srv.Client()})
	_, err := c.Download(context.Background(), "dev_x", "a1", &bytes.Buffer{}, nil)
	var e *Error
	if !errors.As(err, &e) || e.Code != "download_redirect_insecure" {
		t.Fatalf("err = %v", err)
	}
}

func TestDownloadRejectsBadArtifactID(t *testing.T) {
	c, _ := New(Options{BaseURL: "http://127.0.0.1:1"})
	for _, id := range []string{"../x", "", "-a", "a/b"} {
		if _, err := c.Download(context.Background(), "t", id, &bytes.Buffer{}, nil); err == nil {
			t.Errorf("%q accepted", id)
		}
	}
}

func TestDownloadStallIsCut(t *testing.T) {
	old := downloadStall
	downloadStall = 200 * time.Millisecond
	defer func() { downloadStall = old }()
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "100")
		_, _ = w.Write([]byte("part"))
		w.(http.Flusher).Flush()
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	defer srv.Close()
	defer close(release)
	c, _ := New(Options{BaseURL: srv.URL, HTTPClient: srv.Client()})
	start := time.Now()
	_, err := c.Download(context.Background(), "t", "a1", &bytes.Buffer{}, nil)
	if err == nil || time.Since(start) > 5*time.Second {
		t.Fatalf("err = %v after %s", err, time.Since(start))
	}
}

func TestStorageRefusalIsNotADeviceTokenRefusal(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/storage/blob" {
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte(`<Error>expired https://bucket/blob?X-Amz-Signature=abc</Error>`))
			return
		}
		w.Header().Set("Location", "/storage/blob?X-Amz-Signature=abc")
		w.Header().Set(checksumHeader, strings.Repeat("a", 64))
		w.WriteHeader(http.StatusFound)
	}))
	defer srv.Close()
	c, _ := New(Options{BaseURL: srv.URL, HTTPClient: srv.Client()})
	_, err := c.Download(context.Background(), "dev_x", "a1", &bytes.Buffer{}, nil)
	var e *Error
	if !errors.As(err, &e) || e.Code != "storage_error" || IsAuth(err) || strings.Contains(err.Error(), "Signature") {
		t.Fatalf("err = %v (auth %v)", err, IsAuth(err))
	}
}

func TestSoftwareListMustBeAnArray(t *testing.T) {
	for body, wantErr := range map[string]bool{`[]`: false, `{}`: true, `null`: true} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(body))
		}))
		c, _ := New(Options{BaseURL: srv.URL, HTTPClient: srv.Client()})
		items, err := c.Software(context.Background(), "dev_x")
		srv.Close()
		if (err != nil) != wantErr || (!wantErr && items == nil) {
			t.Errorf("%s: items %v, err %v", body, items, err)
		}
	}
}
