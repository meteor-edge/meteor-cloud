package localrpc

import (
	"os/user"
	"strconv"
)

// Groups answers "is this uid in that group?". The real one reads the system
// user database; tests pass a fake.
type Groups interface {
	Member(uid, gid uint32, group string) bool
}

// SystemGroups looks groups up in /etc/passwd and /etc/group (os/user works the
// same with and without cgo). A user counts as a member when the group is their
// primary group or lists them, so adding someone to a group works without a
// new login session.
type SystemGroups struct{}

// Member implements Groups.
func (SystemGroups) Member(uid, gid uint32, group string) bool {
	g, err := user.LookupGroup(group)
	if err != nil {
		return false
	}
	if g.Gid == strconv.FormatUint(uint64(gid), 10) {
		return true
	}
	u, err := user.LookupId(strconv.FormatUint(uint64(uid), 10))
	if err != nil {
		return false
	}
	ids, err := u.GroupIds()
	if err != nil {
		return false
	}
	for _, id := range ids {
		if id == g.Gid {
			return true
		}
	}
	return false
}

// LookupUID returns the uid of a user name.
func LookupUID(name string) (uint32, bool) {
	u, err := user.Lookup(name)
	if err != nil {
		return 0, false
	}
	n, err := strconv.ParseUint(u.Uid, 10, 32)
	return uint32(n), err == nil
}

// LookupGID returns the gid of a group name.
func LookupGID(name string) (int, bool) {
	g, err := user.LookupGroup(name)
	if err != nil {
		return 0, false
	}
	n, err := strconv.Atoi(g.Gid)
	return n, err == nil
}
