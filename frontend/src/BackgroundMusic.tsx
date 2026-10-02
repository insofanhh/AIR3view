import React, {useEffect, useRef} from 'react';
import {Music2, Upload, X} from 'lucide-react';
import './music.css';

export const musicAccept='.wav,.mp3,.m4a,.ogg,.flac,audio/*';

export function MusicUpload({disabled,onUpload}:{disabled?:boolean;onUpload:(file:File)=>void}){
  return <label className={'music-upload '+(disabled?'disabled':'')}><Upload size={14}/> Nhạc nền<input type="file" accept={musicAccept} disabled={disabled} onChange={e=>{const file=e.target.files?.[0];if(file)onUpload(file);e.target.value='';}}/></label>;
}

export function BackgroundMusic({name,src,volume,disabled,onUpload,onRemove,onVolume}:{name?:string;src?:string;volume:number;disabled?:boolean;onUpload:(file:File)=>void;onRemove:()=>void;onVolume:(value:number)=>void}){
  const audio=useRef<HTMLAudioElement>(null);
  useEffect(()=>{if(audio.current)audio.current.volume=Math.max(0,Math.min(1,volume));},[volume,src]);
  return <div className="background-music">
    <div className="music-heading"><strong><Music2 size={15}/> Nhạc nền</strong><MusicUpload disabled={disabled} onUpload={onUpload}/>{name&&<button type="button" aria-label="Bỏ nhạc nền" title="Bỏ nhạc nền" disabled={disabled} onClick={onRemove}><X size={14}/></button>}</div>
    <div className={'music-bed '+(!name?'empty':'')}><Music2 size={14}/><span>{name||'Chưa có nhạc nền'}</span><small>{name?'0 → hết video · tự lặp':''}</small></div>
    {src&&<audio ref={audio} controls preload="metadata" src={src}/>}
    <label className="music-volume">Âm lượng nhạc nền · {Math.round(volume*100)}%<input aria-label="Âm lượng nhạc nền" type="range" min="0" max="1" step=".01" value={volume} disabled={disabled} onChange={e=>onVolume(Number(e.target.value))}/></label>
    <p>WAV, MP3, M4A, OGG, FLAC · tối đa 100 MB. Nhạc tự lặp nếu ngắn hơn video; kéo âm lượng về 0% để tắt.</p>
  </div>;
}
