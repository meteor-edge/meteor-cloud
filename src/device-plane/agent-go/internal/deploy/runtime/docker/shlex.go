package docker

import (
	"strings"

	"meteorcloud/meteorcli/internal/deploy/runtime"
)

// Split splits a command line like Python's shlex.split (POSIX rules, no
// comments): whitespace separates words, 'single quotes' keep everything,
// "double quotes" let a backslash escape only " and \, and a backslash outside
// quotes escapes the next character. The Python agent split spec.command this
// way, so releases keep working unchanged.
func Split(s string) ([]string, error) {
	var (
		words   []string
		word    strings.Builder
		inWord  bool
		quote   rune // 0, '\'' or '"'
		escaped bool
	)
	for _, r := range s {
		switch {
		case escaped:
			if quote == '"' && r != '"' && r != '\\' {
				word.WriteRune('\\')
			}
			word.WriteRune(r)
			escaped, inWord = false, true
		case quote == '\'':
			if r == '\'' {
				quote = 0
			} else {
				word.WriteRune(r)
			}
		case quote == '"':
			switch r {
			case '"':
				quote = 0
			case '\\':
				escaped = true
			default:
				word.WriteRune(r)
			}
		case r == '\\':
			escaped, inWord = true, true
		case r == '\'' || r == '"':
			quote, inWord = r, true
		case r == ' ' || r == '\t' || r == '\n' || r == '\r':
			if inWord {
				words = append(words, word.String())
				word.Reset()
				inWord = false
			}
		default:
			word.WriteRune(r)
			inWord = true
		}
	}
	if quote != 0 {
		return nil, runtime.Failf("No closing quotation")
	}
	if escaped {
		return nil, runtime.Failf("No escaped character")
	}
	if inWord {
		words = append(words, word.String())
	}
	return words, nil
}
