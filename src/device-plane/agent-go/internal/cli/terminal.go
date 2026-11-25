package cli

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"unsafe"
)

// readSecret reads one line from in. On a terminal nothing typed is shown, and
// the terminal is put back before this returns, also after Ctrl-C.
func readSecret(ctx context.Context, in io.Reader, prompt io.Writer) (string, error) {
	f, ok := in.(*os.File)
	if !ok {
		return readLine(in)
	}
	old, err := terminal(f)
	if err != nil { // a pipe or a file
		return readLine(in)
	}
	// SIGINT and SIGTERM already end ctx (see Main).
	ctx, stop := signal.NotifyContext(ctx, syscall.SIGHUP, syscall.SIGQUIT)
	defer stop()
	hidden := old
	hidden.Lflag &^= syscall.ECHO
	if err := setTerminal(f, hidden); err != nil {
		return "", fmt.Errorf("hide the token: %w", err)
	}
	defer func() {
		_ = setTerminal(f, old)
		fmt.Fprintln(prompt)
	}()
	type answer struct {
		line string
		err  error
	}
	done := make(chan answer, 1)
	go func() {
		line, err := readLine(f)
		done <- answer{line, err}
	}()
	select {
	case a := <-done:
		return a.line, a.err
	case <-ctx.Done():
		return "", errors.New("interrupted")
	}
}

func readLine(in io.Reader) (string, error) {
	line, err := bufio.NewReader(io.LimitReader(in, 4096)).ReadString('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return "", err
	}
	return line, nil
}

// terminal returns the settings of the terminal f, an error when it is none.
func terminal(f *os.File) (syscall.Termios, error) {
	var t syscall.Termios
	return t, ioctl(f, syscall.TCGETS, &t)
}

func setTerminal(f *os.File, t syscall.Termios) error { return ioctl(f, syscall.TCSETS, &t) }

// ioctl is what golang.org/x/term does, without the dependency.
func ioctl(f *os.File, request uintptr, t *syscall.Termios) error {
	raw, err := f.SyscallConn()
	if err != nil {
		return err
	}
	var errno syscall.Errno
	if err := raw.Control(func(fd uintptr) {
		_, _, errno = syscall.Syscall(syscall.SYS_IOCTL, fd, request, uintptr(unsafe.Pointer(t)))
	}); err != nil {
		return err
	}
	if errno != 0 {
		return errno
	}
	return nil
}
