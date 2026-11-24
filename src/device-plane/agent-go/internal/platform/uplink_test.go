package platform

import "testing"

const routeHeader = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"

func TestUplink(t *testing.T) {
	for _, c := range []struct {
		name  string
		files map[string]string
		want  Uplink
	}{
		{"none", map[string]string{"/proc/net/route": routeHeader}, Uplink{}},
		{"ethernet wins on metric", map[string]string{
			"/proc/net/route": routeHeader +
				"wlan0\t00000000\t0100A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n" +
				"eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n" +
				"eth0\t0000A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n",
			"/sys/class/net/eth0/type":   "1\n",
			"/sys/class/net/eth0/device": "",
		}, Uplink{Type: "ethernet", Interface: "eth0"}},
		{"wifi", map[string]string{
			"/proc/net/route":             routeHeader + "wlan0\t00000000\t0100A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n",
			"/sys/class/net/wlan0/uevent": "INTERFACE=wlan0\nDEVTYPE=wlan\n",
		}, Uplink{Type: "wifi", Interface: "wlan0"}},
		{"down route skipped", map[string]string{
			"/proc/net/route": routeHeader + "eth0\t00000000\t0100A8C0\t0002\t0\t0\t100\t00000000\t0\t0\t0\n",
		}, Uplink{}},
		{"cellular", map[string]string{
			"/proc/net/route": routeHeader + "wwan0\t00000000\t00000000\t0001\t0\t0\t700\t00000000\t0\t0\t0\n",
		}, Uplink{Type: "cellular", Interface: "wwan0"}},
		{"tunnel", map[string]string{
			"/proc/net/route": routeHeader + "tun0\t00000000\t00000000\t0001\t0\t0\t50\t00000000\t0\t0\t0\n",
		}, Uplink{Type: "other", Interface: "tun0"}},
	} {
		if got := fakeHost(t, c.files).Uplink(); got != c.want {
			t.Errorf("%s: %+v", c.name, got)
		}
	}
}
