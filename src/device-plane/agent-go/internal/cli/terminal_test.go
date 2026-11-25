package cli

import (
	"context"
	"io"
	"os"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"
)

// pty returns both ends of a new terminal.
func pty(t *testing.T) (master, slave *os.File) {
	t.Helper()
	fd, err := syscall.Open("/dev/ptmx", syscall.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC, 0)
	if err != nil {
		t.Skipf("no terminals here: %v", err)
	}
	var unlock, number int32
	for _, c := range []struct {
		request uintptr
		value   *int32
	}{{syscall.TIOCSPTLCK, &unlock}, {syscall.TIOCGPTN, &number}} {
		if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), c.request, uintptr(unsafe.Pointer(c.value))); errno != 0 {
			t.Fatal(errno)
		}
	}
	master = os.NewFile(uintptr(fd), "ptmx")
	slave, err = os.OpenFile("/dev/pts/"+strconv.Itoa(int(number)), os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { slave.Close(); master.Close() })
	return master, slave
}

func echo(t *testing.T, f *os.File) bool {
	t.Helper()
	s, err := terminal(f)
	if err != nil {
		t.Fatal(err)
	}
	return s.Lflag&syscall.ECHO != 0
}

func waitHidden(t *testing.T, f *os.File) {
	t.Helper()
	for deadline := time.Now().Add(5 * time.Second); echo(t, f); time.Sleep(time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the terminal still shows what is typed")
		}
	}
}

func TestSecretIsNotShownOnATerminal(t *testing.T) {
	master, slave := pty(t)
	type answer struct {
		line string
		err  error
	}
	done := make(chan answer, 1)
	go func() {
		line, err := readSecret(context.Background(), slave, io.Discard)
		done <- answer{line, err}
	}()
	waitHidden(t, slave)
	if _, err := master.WriteString("rgt_secret\n"); err != nil {
		t.Fatal(err)
	}
	if a := <-done; a.err != nil || a.line != "rgt_secret\n" {
		t.Errorf("%q, %v", a.line, a.err)
	}
	if !echo(t, slave) {
		t.Error("the terminal stays hidden")
	}
	// What the terminal showed up to here: the line break at most.
	if _, err := slave.WriteString("END\n"); err != nil {
		t.Fatal(err)
	}
	var shown strings.Builder
	buf := make([]byte, 256)
	for !strings.Contains(shown.String(), "END") {
		n, err := master.Read(buf)
		if err != nil {
			t.Fatal(err)
		}
		shown.Write(buf[:n])
	}
	if strings.Contains(shown.String(), "secret") {
		t.Errorf("shown: %q", shown.String())
	}
}

func TestInterruptPutsTheTerminalBack(t *testing.T) {
	_, slave := pty(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := readSecret(ctx, slave, io.Discard)
		done <- err
	}()
	waitHidden(t, slave)
	cancel()
	if err := <-done; err == nil {
		t.Error("no error")
	}
	if !echo(t, slave) {
		t.Error("the terminal stays hidden")
	}
}

func TestSecretFromAPipe(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	_, _ = w.WriteString("rgt_piped")
	w.Close()
	for _, in := range []io.Reader{r, strings.NewReader("rgt_piped")} {
		if line, err := readSecret(context.Background(), in, io.Discard); err != nil || line != "rgt_piped" {
			t.Errorf("%q, %v", line, err)
		}
	}
}
