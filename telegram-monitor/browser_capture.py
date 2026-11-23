"""Candidate DOM capture for an independently verified, already-connected Web K page.

No API session creation, credential handling, login, sender or startup scheduling.
Requires an external live verifier of account/source/membership on the same page.
"""
from pathlib import Path
import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Awaitable, Callable

import monitor
import browser_context
from browser_ingest import BrowserAccessError, BrowserIdentity, BrowserIntake, BrowserMutation

OBSERVER = Path(__file__).with_name('browser_observer.js').read_text(encoding='utf-8')


@dataclass(frozen=True)
class DomContract:
    root_selector: str
    dom_peer_id: str
    selectors_verified: bool = False


class BrowserCapture:
    def __init__(self, page, intake: BrowserIntake, contract: DomContract,
                 verify_live: Callable[[], Awaitable[BrowserIdentity]]):
        if not contract.selectors_verified:
            raise BrowserAccessError('DOM contract must be independently verified before capture')
        if not contract.root_selector or contract.root_selector in ('body', 'html', '*'):
            raise BrowserAccessError('A single scoped message root is required')
        # Web K uses a negative channel ID; Bot API/Telethon pins use marked peer IDs.
        if intake.peer_id >= -10**12 or str(intake.peer_id + 10**12) != contract.dom_peer_id:
            raise BrowserAccessError('DOM channel must match the pinned supergroup')
        self.page, self.intake, self.contract, self.verify_live = page, intake, contract, verify_live
        self.active = False
        self.last_changes = []

    async def arm(self):
        if self.active:
            raise BrowserAccessError('Stop existing capture before rearming')
        self.intake.pause()
        stage='identity'
        try:
            proof = await asyncio.wait_for(self.verify_live(), timeout=10)
            if proof.page_url != self.page.url:
                raise BrowserAccessError('Live verifier must describe this exact page')
            self.intake.verify(proof)
            stage='observer_install'
            started = await asyncio.wait_for(self.page.evaluate(OBSERVER, {
                'rootSelector': self.contract.root_selector,
                'domPeerId': self.contract.dom_peer_id,
                'maxQueue': 128,
            }),timeout=10)
            stage='baseline_commit'
            self.intake.arm(proof, started['baseline_ids'], dt.datetime.fromtimestamp(started['started_epoch_ms']/1000,monitor.UTC))
        except Exception as error:
            code=next((value for value in ('observer_already_installed','unique_message_root_required','invalid_peer','visible_baseline_required') if value in str(error)),'unconfirmed')
            await self.stop()
            raise BrowserAccessError('Capture baseline invalid: '+stage+':'+code) from None
        self.active = True

    async def drain(self):
        self.last_changes=[]
        if not self.active:
            raise BrowserAccessError('Capture is not active')
        stage='before_identity'
        try:
            proof = await asyncio.wait_for(self.verify_live(),timeout=10)
            if proof.page_url != self.page.url:
                raise BrowserAccessError('Page changed')
            self.intake.verify(proof)
            stage='drain'
            batch = await asyncio.wait_for(self.page.evaluate('() => window.__maxmadeReadOnlyObserver?.drain()'),timeout=10)
            if not batch or batch.get('failure') or not batch.get('root_alive'):
                raise BrowserAccessError('Browser source stopped or overflowed')
            contexts=await asyncio.wait_for(self.page.evaluate(browser_context.SCRIPT,{
                'rootSelector':self.contract.root_selector,'domPeerId':self.contract.dom_peer_id,'events':batch['events']}),timeout=10) if batch['events'] else []
            after = await asyncio.wait_for(self.verify_live(),timeout=10)
            self.intake.verify(after)
            if after != proof or after.page_url != self.page.url:
                raise BrowserAccessError('Identity changed during browser snapshot')
            accepted = []
            for index,raw in enumerate(batch['events']):
                event=BrowserMutation(raw['message_id'],raw['body'],
                    dt.datetime.fromtimestamp(raw['published_epoch'],monitor.UTC),
                    dt.datetime.fromtimestamp(raw['observed_epoch_ms']/1000,monitor.UTC),raw['kind'],
                    contexts[index] if isinstance(contexts,list) and index<len(contexts) else None)
                before=self.intake.db.execute('select edit_version from events where source_id=? and message_id=?',
                    (self.intake.source_id,event.message_id)).fetchone()
                candidate=self.intake.accept(proof,event)
                if candidate is not None:accepted.append(candidate)
                if candidate is not None:
                    after=self.intake.db.execute('select edit_version from events where source_id=? and message_id=?',
                        (self.intake.source_id,event.message_id)).fetchone()
                    if after and (before is None or before[0]!=after[0]):
                        self.last_changes.append({'message_id':event.message_id,'version':after[0],
                            'change':'first_ingest' if before is None else 'edited','observed_kind':event.kind,
                            'observed_at':monitor.iso(event.observed_at)})
            return accepted
        except Exception as error:
            # An explicit network pause before touching the queue is retryable.
            # A failed post-drain check must disarm: its batch is unconfirmed.
            if stage=='before_identity' and isinstance(error,ConnectionError):raise
            await self.stop()
            raise BrowserAccessError('Browser source disarmed; inspect local status without message bodies') from None

    async def stop(self):
        self.active = False
        self.intake.pause()
        try:
            await asyncio.wait_for(self.page.evaluate('() => { window.__maxmadeReadOnlyObserver?.stop(); delete window.__maxmadeReadOnlyObserver; }'),timeout=5)
        except Exception:
            pass # Disconnection cannot be mistaken for a successful paid-session termination.
