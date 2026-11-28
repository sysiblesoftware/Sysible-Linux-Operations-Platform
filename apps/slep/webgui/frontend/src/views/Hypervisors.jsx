import React, { useEffect, useState } from 'react'
import { api, canWrite } from '../api.js'
import { Field, Modal, useErr, CopyButton } from '../ui.jsx'

// A hypervisor is a HOST, not a property of one project.
//
// It used to be reachable only from inside the Build-infrastructure wizard: pick
// the libvirt provider, then pick "Remote KVM host over SSH", and only then did
// the buttons that install libvirt, repair the storage permissions and explain a
// failed boot appear. Nothing in the rail said hypervisors existed at all --
// reported, in as many words, as "I don't see a hypervisor option on SLEP" -- and
// every project re-typed the same host, user and URI by hand.
//
// So they live here, beside Jump Hosts and Controllers, which are the same kind
// of thing: something out on the network that SLEP reaches into.
//
// NO PASSWORD IS SAVED. SLEP's managed key is the way in; a password is typed at
// the moment it is needed -- installing that key the first time, or sudo for a
// host action -- and is never written down. Saving one would create a store of
// SSH passwords to operator-chosen hosts, which is a much bigger thing than
// saving some typing.
export default function Hypervisors() {
  const [rows, setRows] = useState([])
  const [add, setAdd] = useState(false)
  const [edit, setEdit] = useState(null)
  const [manage, setManage] = useState(null)
  const load = () => api('hypervisors').then((d) => setRows(d.hypervisors || [])).catch(() => {})
  useEffect(() => { load() }, [])
  const writable = canWrite()
  return (
    <>
      <h2>Hypervisors</h2>
      <div className="muted" style={{ marginBottom: 10 }}>
        The KVM/libvirt hosts this platform builds VMs on. Save one here and every project
        can pick it instead of retyping the address — and the host-level jobs
        (install libvirt, repair storage permissions, explain a VM that never got an address)
        all run from <b>Manage</b>, without hunting through a project wizard.
      </div>
      {writable && (
        <div className="row" style={{ marginBottom: 12 }}>
          <button className="primary" onClick={() => setAdd(true)}>+ Add hypervisor</button>
        </div>
      )}
      {rows.length === 0 ? (
        <div className="muted">
          No hypervisors yet. Add one — for the SLEP host itself use
          <span className="mono"> qemu:///system</span>, or
          <span className="mono"> qemu+ssh://user@host/system</span> for a remote KVM box.
        </div>
      ) : (
        <table>
          <thead>
            <tr><th>Name</th><th>Connection</th><th>Network / Pool</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <tr key={h.id}>
                <td>{h.name}</td>
                <td className="mono muted" style={{ maxWidth: 360, overflowWrap: 'anywhere' }}>{h.uri}</td>
                <td className="muted">{h.network} / {h.pool}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                  <button className="ghost sm" onClick={() => setManage(h)}>Manage</button>
                  {writable && <button className="ghost sm" style={{ marginLeft: 6 }} onClick={() => setEdit(h)}>Edit</button>}
                  {writable && (
                    <button className="danger ghost sm" style={{ marginLeft: 6 }}
                      onClick={async () => {
                        if (!confirm(`Forget hypervisor “${h.name}”?\n\nNothing on the host is touched and no project changes — `
                          + `a project's connection URI lives in its own variables.tf, so an apply keeps working.`)) return
                        await api('hypervisors/' + h.id, { method: 'DELETE' }); load()
                      }}>Forget</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {add && <HypervisorForm onClose={() => setAdd(false)} onDone={() => { setAdd(false); load() }} />}
      {edit && <HypervisorForm hv={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); load() }} />}
      {manage && <ManageHypervisor hv={manage} onClose={() => setManage(null)} />}
    </>
  )
}

// Local vs remote decides the URI, exactly as the wizard's connection block does.
function HypervisorForm({ hv, onClose, onDone }) {
  const remote = /^qemu\+/.test(hv?.uri || '')
  const [name, setName] = useState(hv?.name || '')
  const [mode, setMode] = useState(hv ? (remote ? 'remote' : 'local') : 'remote')
  const [host, setHost] = useState(hv?.host || '')
  const [username, setUsername] = useState(hv?.username || 'root')
  const [port, setPort] = useState(String(hv?.port || 22))
  const [network, setNetwork] = useState(hv?.network || 'default')
  const [pool, setPool] = useState(hv?.pool || 'default')
  const { wrap, node } = useErr()

  // NO HOST, NO URI. Assembling qemu+ssh://user@/system with the host field empty
  // produces a URI libvirt cannot parse; it falls back to its default and the
  // operator gets "connect to host localhost port 22: Connection refused", which
  // names neither the real problem nor this field.
  const uri = mode === 'local'
    ? 'qemu:///system'
    : (host.trim() ? `qemu+ssh://${username.trim() || 'root'}@${host.trim()}${port && port !== '22' ? ':' + port : ''}/system?no_verify=1` : '')

  return (
    <Modal title={hv ? `Edit ${hv.name}` : 'Add hypervisor'} onClose={onClose}>
      <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="kvm-1" /></Field>
      <Field label="Where it runs">
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="local">This host (qemu:///system)</option>
          <option value="remote">Remote KVM host over SSH</option>
        </select>
      </Field>
      {mode === 'remote' && (
        <>
          <Field label="Host (address)"><input value={host} onChange={(e) => setHost(e.target.value)} placeholder="kvm-1.lan" /></Field>
          <div className="row" style={{ gap: 10 }}>
            <Field label="SSH username"><input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="root" /></Field>
            <Field label="Port"><input value={port} onChange={(e) => setPort(e.target.value)} placeholder="22" /></Field>
          </div>
        </>
      )}
      <div className="row" style={{ gap: 10 }}>
        <Field label="Network"><input value={network} onChange={(e) => setNetwork(e.target.value)} placeholder="default" /></Field>
        <Field label="Storage pool"><input value={pool} onChange={(e) => setPool(e.target.value)} placeholder="default" /></Field>
      </div>
      <Field label="Connection URI (assembled)">
        <input value={uri} readOnly style={{ fontFamily: 'ui-monospace,monospace', fontSize: 12, opacity: 0.85 }} />
      </Field>
      <div className="faint" style={{ fontSize: 11.5, marginTop: -4 }}>
        No password is stored. SLEP uses its managed key; you are asked for a password only
        when one is genuinely needed, and it is used once.
      </div>
      {node}
      <div className="row" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
        <button className="ghost sm" onClick={onClose}>Cancel</button>
        <button className="primary sm" disabled={!name.trim() || !uri} onClick={() => wrap(async () => {
          const body = {
            name: name.trim(), uri, network: network.trim() || 'default', pool: pool.trim() || 'default',
            host: mode === 'remote' ? host.trim() : '',
            username: username.trim() || 'root', port: Number(port) || 22,
          }
          if (hv) await api('hypervisors/' + hv.id, { method: 'PATCH', json: body })
          else await api('hypervisors', { method: 'POST', json: body })
          onDone()
        })}>Save</button>
      </div>
    </Modal>
  )
}

// Everything that is done TO a host, in one place, with the host already chosen.
const ACTIONS = [
  ['infra/hypervisor-provision', '\u{1F6E0} Make this a hypervisor', false,
    'Install qemu-kvm, libvirt and virtinst for this host’s distribution, start the daemon, and define the default pool and network. Safe to re-run.'],
  ['infra/hypervisor-permissions', '\u{1F50E} Check storage permissions', false,
    'Report why a VM cannot open its own disk — probing as the VM process is really credentialed, not as root. Changes nothing.'],
  ['infra/hypervisor-why-no-ip', '\u{1F4A1} Why did a VM get no IP?', false,
    'Read each VM’s state, its DHCP lease, qemu’s own log and any security denial, and say which one explains it. Changes nothing.'],
  ['infra/hypervisor-fix-permissions', '\u{1F527} Fix permissions', true,
    'Make the pool traversable and every volume readable by the VM process, clear the owner libvirt saved, and allow qemu to read the pool.'],
]

function ManageHypervisor({ hv, onClose }) {
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState('')
  const [res, setRes] = useState(null)
  const { wrap, node } = useErr()
  const writable = canWrite()

  const run = (path) => wrap(async () => {
    setBusy(path); setRes(null)
    try {
      setRes(await api(path, {
        method: 'POST',
        json: {
          hypervisor_id: hv.id, password: pw.trim(),
          pool_dir: '/var/lib/libvirt/images',
          prefix: '', network: hv.network || 'default',
          set_remember_owner: true, fix_apparmor: true,
        },
      }))
    } catch (e) { setRes({ ok: false, steps: [], output: String(e.message || e) }) } finally { setBusy('') }
  })

  return (
    <Modal title={`Manage ${hv.name}`} onClose={onClose}>
      <div className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>
        These run over SSH on <span className="mono">{(hv.username || 'root')}@{hv.host || 'this host'}</span>.
        The read-only checks need sudo there; the password is used for that one call and never saved.
      </div>
      <Field label="Host password (used once — for sudo; or vault.NAME)">
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="off" />
      </Field>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
        {ACTIONS.filter((a) => writable || !a[2]).map(([path, label, , title]) => (
          <button key={path} className="ghost sm" title={title} disabled={!!busy}
            onClick={() => run(path)}>{busy === path ? 'Working…' : label}</button>
        ))}
      </div>
      {busy && (
        <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
          Working on {hv.host || 'this host'}… installing packages over a slow link can take a few minutes.
        </div>
      )}
      {node}
      {res && (
        <div style={{ marginTop: 10 }}>
          <div className="row" style={{ alignItems: 'flex-start', gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: res.ok ? 'var(--green-bright)' : 'var(--danger)' }}>
              {res.ok ? '✓ ' : '✗ '}{res.output}
            </div>
            <CopyButton label="Copy result" title="Copy every step and its detail"
              text={() => [res.output, ''].concat((res.steps || []).map(
                (st) => `${st.status === 'failed' ? '✗' : '✓'} ${st.label}: ${st.detail}`)).join('\n')} />
          </div>
          {(res.steps || []).map((st, i) => (
            <div key={i} className="row" style={{ gap: 8, fontSize: 12, alignItems: 'flex-start', marginTop: 3 }}>
              <span style={{ width: 12, flex: '0 0 auto', color: st.status === 'failed' ? 'var(--danger)' : 'var(--green-bright)' }}>
                {st.status === 'failed' ? '✗' : '✓'}
              </span>
              <span style={{ width: 200, flex: '0 0 auto' }}>{st.label}</span>
              <span className="muted" style={{ whiteSpace: 'pre-wrap', flex: 1, minWidth: 0 }}>{st.detail}</span>
            </div>
          ))}
        </div>
      )}
      <div className="row" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
        <button className="ghost sm" onClick={onClose}>Close</button>
      </div>
    </Modal>
  )
}
