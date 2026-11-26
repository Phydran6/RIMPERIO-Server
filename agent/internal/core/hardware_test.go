package core

import "testing"

func TestParseThrottled(t *testing.T) {
	raw, texts := ParseThrottled("throttled=0x50005\n")
	if raw != "0x50005" {
		t.Fatalf("raw = %q", raw)
	}
	want := map[string]bool{"Unterspannung jetzt": true, "gedrosselt jetzt": true,
		"Unterspannung seit dem Start": true, "gedrosselt seit dem Start": true}
	if len(texts) != len(want) {
		t.Fatalf("texts = %v", texts)
	}
	for _, x := range texts {
		if !want[x] {
			t.Fatalf("unerwartet: %s", x)
		}
	}
	if raw, texts := ParseThrottled("throttled=0x0"); raw != "0x0" || len(texts) != 0 {
		t.Fatalf("0x0 -> %q %v", raw, texts)
	}
}
