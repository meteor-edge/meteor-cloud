package dsl

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// The examples in contracts/rules-v1.examples.json are documentation and test data
// for both planes: they must stay valid here.
func TestContractExamplesAreValid(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "..", "..", "contracts", "rules-v1.examples.json"))
	if err != nil {
		t.Skipf("contracts directory not available: %v", err)
	}
	var examples []json.RawMessage
	if err := json.Unmarshal(raw, &examples); err != nil {
		t.Fatal(err)
	}
	if len(examples) < 5 {
		t.Fatalf("only %d examples", len(examples))
	}
	for i, ex := range examples {
		if _, err := ParseAndValidate(ex, DefaultLimits(), false); err != nil {
			t.Errorf("example %d: %v", i, err)
		}
	}
}
