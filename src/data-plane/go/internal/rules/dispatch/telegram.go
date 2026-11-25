package dispatch

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

// TelegramAPI is the default Bot API server.
const TelegramAPI = "https://api.telegram.org"

var (
	telegramToken = regexp.MustCompile(`^[0-9]{5,20}:[A-Za-z0-9_-]{20,100}$`)
	telegramChat  = regexp.MustCompile(`^(-?[0-9]{1,20}|@[A-Za-z0-9_]{4,64})$`)
)

// Telegram sends messages as an organization's own bot.
type Telegram struct {
	API    string
	Client *http.Client
}

// Send posts plain text (no markup, so device and rule names cannot inject any).
func (t *Telegram) Send(ctx context.Context, token, chat, text string) error {
	if !telegramToken.MatchString(token) || !telegramChat.MatchString(chat) {
		return Permanent(errors.New("the Telegram bot token or chat id is not valid"))
	}
	if r := []rune(text); len(r) > 4000 {
		text = string(r[:4000]) + "…"
	}
	body, _ := json.Marshal(map[string]any{"chat_id": chat, "text": text,
		"link_preview_options": map[string]bool{"is_disabled": true}})
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	api := t.API
	if api == "" {
		api = TelegramAPI
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(api, "/")+"/bot"+token+"/sendMessage", bytes.NewReader(body))
	if err != nil {
		return Permanent(errors.New("the Telegram API address is not valid"))
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := t.Client.Do(req)
	if err != nil {
		// The token is part of the URL, and url.Error prints the URL: keep only the cause.
		var ue *url.Error
		if errors.As(err, &ue) {
			err = ue.Err
		}
		return fmt.Errorf("telegram: %w", err)
	}
	defer resp.Body.Close()
	return statusError("telegram", resp, true)
}
