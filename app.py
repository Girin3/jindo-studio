import streamlit as st
import os
PROJECTS_DIR = os.getenv('JINDO_PROJECTS_DIR', 'projects')
os.makedirs(PROJECTS_DIR, exist_ok=True)
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import re, json, os, uuid, datetime, html, base64, hashlib, wave, contextlib, subprocess, shutil, glob

st.set_page_config(page_title='Jindo Studio', page_icon='🐕', layout='wide', initial_sidebar_state='collapsed')

st.markdown('''<style>
.block-container{max-width:1100px;padding-top:1.2rem;padding-bottom:3rem}
.stButton>button{min-height:48px;border-radius:12px;font-weight:700}
.stTextArea textarea,.stTextInput input{font-size:16px}
@media (max-width:700px){.block-container{padding-left:.8rem;padding-right:.8rem}.stColumns{gap:.35rem}h1{font-size:1.75rem!important}}
</style>''', unsafe_allow_html=True)

DEFAULT_RULES = {
    'shorts_chars': '약 250자', 'shorts_ratio': '9:16', 'longform_ratio': '16:9',
    'audience': '한국 50대 이상 중심', 'hook': '도입 의문형 후킹',
    'ending': '마지막 경험형 댓글 질문',
    'image_style': 'documentary realism, Nikon D850, 8K, natural light, no text',
    'coat_colors': ['흰색','황갈색','검은색','검정+황갈 무늬'],
    'excluded': ['회색/늑대색','브린들','병원 장면'],
    'people': '사람이 꼭 필요할 때만 한국 50~60대',
    'opening': '쇼츠 오프닝 첫 장면은 진돗개 단독, 사람 없음',
    'screen_terms': '한국어 화면 노출에서 동물 지칭은 진돗개로 통일',
    'image_rule': '핵심 행동·원인·결과가 이미지 자체만으로 이해되도록 구성',
    'locale': 'ko-KR', 'language_style': '한국 자연 구어체'
}

JAPAN_RULES = dict(DEFAULT_RULES, **{
    'audience':'일본 40~60대 중심, 30대도 어색하지 않게',
    'locale':'ja-JP',
    'language_style':'일본 현지 구어체·SNS/YouTube 내레이션. 한국어 직역 금지',
    'people':'사람이 꼭 필요할 때만 일본 50~60대',
    'screen_terms':'일본어 화면 문구는 현지화하고 부자연스러운 직역 표현 금지'
})

def active_rules(name):
    return JAPAN_RULES if name=='일본용' else DEFAULT_RULES

def estimate_work(project):
    kinds=[]
    if project.get('output') in ('쇼츠','둘 다'): kinds.append('shorts')
    if project.get('output') in ('롱폼','둘 다'): kinds.append('longform')
    scenes=sum(len(project.get(k,{}).get('scenes',[])) for k in kinds)
    return {'image_calls_max':scenes,'tts_calls':len(kinds),'render_outputs':len(kinds),
            'note':'캐시된 이미지는 재호출하지 않아 실제 이미지 API 호출 수는 더 적을 수 있습니다.'}


SYSTEM_PROMPT = '''당신은 Jindo Studio의 콘텐츠 엔진이다. 진돗개 정보 영상을 만든다.
반드시 원본 레퍼런스 문장을 베끼지 말고, 주제/구성 원리만 참고해 신규 표현으로 작성한다.
사실과 추정을 구분하고 과장된 견종 우열, 공격성 단정, 의학적 진단을 피한다.
웹 검색 결과가 있다면 신뢰도 높은 출처를 우선하여 검증한다.
출력은 설명문 없이 유효한 JSON 하나만 반환한다.'''

def youtube_id(value):
    try:
        u=urlparse(value.strip())
        host=u.netloc.lower()
        if host in ('youtu.be','www.youtu.be'):
            return u.path.strip('/').split('/')[0]
        if 'youtube.com' in host:
            if u.path == '/watch': return parse_qs(u.query).get('v',[None])[0]
            m=re.search(r'/(?:shorts|embed)/([^/?]+)',u.path)
            if m: return m.group(1)
    except Exception: pass
    return None

def fetch_oembed(video_url):
    endpoint='https://www.youtube.com/oembed?format=json&url='+video_url
    try:
        req=Request(endpoint, headers={'User-Agent':'Mozilla/5.0'})
        with urlopen(req, timeout=8) as r: d=json.loads(r.read().decode('utf-8'))
        return {'status':'ok','title':html.unescape(d.get('title','')),'author_name':d.get('author_name',''),
                'author_url':d.get('author_url',''),'thumbnail_url':d.get('thumbnail_url','')}
    except (URLError, HTTPError, TimeoutError, ValueError) as e:
        return {'status':'unavailable','reason':str(e)}

def reference_plan(source, vid, meta, ref_text=''):
    if not vid:
        return {'input_kind':'직접 소재','topic':source.strip(), 'reference_text_supplied':bool(ref_text.strip()),
                'analysis_note':'직접 입력 소재를 중심으로 독립적인 진돗개 정보 콘텐츠를 구성',
                'copyright_rule':'신규 구성과 신규 표현 사용'}
    title = meta.get('title') if meta.get('status')=='ok' else ''
    return {'input_kind':'YouTube 레퍼런스','video_id':vid,'reference_title':title or '(메타데이터 미수신)',
            'topic':title or source.strip(),'reference_text_supplied':bool(ref_text.strip()),
            'analysis_note':'공개 메타데이터와 사용자가 제공한 레퍼런스 텍스트가 있으면 이를 분석하되 원문은 복제하지 않음',
            'copyright_rule':'아이디어·후킹·전개 원리만 참고하고 대본은 신규 작성'}

def fallback_content(topic, kind):
    if kind=='shorts':
        return {'title':f'진돗개, 왜 이런 행동을 할까요?','thumbnail':'진돗개\n왜 이럴까?',
                'script':f'[AI 연결 필요] {topic[:70]} 주제를 바탕으로 약 250자의 신규 쇼츠 대본을 생성합니다.',
                'scenes':[{'scene':1,'narration':'오프닝 후킹','visual':'진돗개 단독 행동 장면','ratio':'9:16','image_prompt':'Authentic Korean Jindo alone, documentary realism, vertical 9:16, no text'}]}
    return {'title':f'진돗개 정보 다큐 | {topic[:35]}','thumbnail':'진돗개\n알고 계셨나요?',
            'script':f'[AI 연결 필요] {topic[:100]} 주제를 검증 자료와 함께 롱폼으로 구성합니다.',
            'chapters':['도입','배경','핵심 정보','사례','정리'],
            'scenes':[{'scene':1,'narration':'도입','visual':'진돗개 단독 도입 장면','ratio':'16:9','image_prompt':'Authentic Korean Jindo, documentary realism, horizontal 16:9, no text'}]}

def strip_json_fence(text):
    t=text.strip()
    t=re.sub(r'^```(?:json)?\s*','',t,flags=re.I)
    t=re.sub(r'\s*```$','',t)
    return t.strip()

def ai_generate(api_key, model, topic, output, ref, ref_text):
    from openai import OpenAI
    client=OpenAI(api_key=api_key)
    wanted=[]
    if output in ('쇼츠','둘 다'): wanted.append('shorts')
    if output in ('롱폼','둘 다'): wanted.append('longform')
    schema_note='''반환 JSON 구조:
{
 "reference_insights":{"core_topic":"","hook_pattern":"","structure_pattern":"","do_not_copy":[""]},
 "fact_check":{"summary":"","verified_points":[{"claim":"","status":"verified|uncertain|context_needed","note":""}],"source_notes":[""]},
 "shorts":{"title":"","thumbnail":"","script":"","scenes":[{"scene":1,"narration":"","visual":"","coat_color":"흰색|황갈색|검은색|검정+황갈 무늬","ratio":"9:16","image_prompt":"English prompt"}]},
 "longform":{"title":"","thumbnail":"","script":"","chapters":[{"title":"","summary":""}],"scenes":[{"scene":1,"narration":"","visual":"","coat_color":"","ratio":"16:9","image_prompt":"English prompt"}]}
}
요청하지 않은 shorts/longform 키는 생략한다.'''
    prompt=f'''주제: {topic}\n제작 유형: {", ".join(wanted)}\n레퍼런스 분석 정보: {json.dumps(ref, ensure_ascii=False)}\n레퍼런스 텍스트(없을 수 있음): {ref_text[:12000]}\n\n고정 제작 규칙: {json.dumps(ref.get('preset_rules', DEFAULT_RULES), ensure_ascii=False)}\n\n쇼츠는 한국어 자연 구어체 한 문단, 약 250자, 첫 문장은 강한 의문형 후킹, 팩트 1~2개, 마지막은 경험형 댓글 질문. 장면은 의미 단위로 충분히 나눈다.\n롱폼은 50대 이상이 듣기 편한 1인 낭독형 다큐 구어체로 도입-배경-핵심 정보-사례/오해-정리 구조. 장면별 이미지 프롬프트를 만든다.\n이미지 프롬프트는 영어로 쓰고 모든 장면에 authentic Korean Jindo를 포함한다. 회색/늑대색/브린들 금지, 병원 장면 금지.\n웹 검색으로 핵심 사실을 검증하되 불확실한 내용은 uncertain으로 표시한다.\n{schema_note}'''
    print(f"[OPENAI DEBUG] model={model}, key_present={bool(api_key)}", flush=True)
        
    resp=client.responses.create(model=model, tools=[{'type':'web_search'}], input=[
            {'role':'system','content':SYSTEM_PROMPT}, {'role':'user','content':prompt}
            ])
    raw=resp.output_text
    return json.loads(strip_json_fence(raw)), raw
        
def image_cache_path(project_id, kind, scene_no, prompt):
    key=hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:12]
    folder=os.path.join(PROJECTS_DIR,project_id,'images',kind)
    os.makedirs(folder,exist_ok=True)
    return os.path.join(folder,f'scene_{scene_no:03d}_{key}.png')

def generate_image(api_key, project_id, kind, scene, force=False):
    from openai import OpenAI
    prompt=scene.get('image_prompt','').strip()
    if not prompt: raise ValueError('이미지 프롬프트가 없습니다.')
    no=int(scene.get('scene',1))
    path=image_cache_path(project_id,kind,no,prompt)
    if os.path.exists(path) and not force:
        return path, True
    client=OpenAI(api_key=api_key)
    ratio=scene.get('ratio','9:16')
    size='1024x1536' if ratio=='9:16' else '1536x1024'
    result=client.images.generate(model=st.session_state.get('image_model_runtime','gpt-image-2'), prompt=prompt, size=size)
    item=result.data[0]
    b64=getattr(item,'b64_json',None)
    if not b64:
        raise RuntimeError('이미지 데이터가 반환되지 않았습니다.')
    with open(path,'wb') as f: f.write(base64.b64decode(b64))
    return path, False

def render_image_panel(project, kind, api_key):
    content=project.get(kind,{})
    scenes=content.get('scenes',[])
    if not scenes: return
    st.subheader('장면 이미지')
    for idx,scene in enumerate(scenes):
        no=int(scene.get('scene',idx+1))
        prompt=scene.get('image_prompt','')
        cache=image_cache_path(project['project_id'],kind,no,prompt) if prompt else ''
        with st.expander(f'장면 {no} · {scene.get("coat_color","")}', expanded=(idx==0)):
            st.write(scene.get('visual',''))
            st.code(prompt,language=None)
            if cache and os.path.exists(cache): st.image(cache,use_container_width=True)
            c1,c2=st.columns(2)
            with c1:
                if st.button(f'이미지 생성 #{no}',key=f'gen_{kind}_{no}',disabled=not bool(api_key)):
                    try:
                        path,cached=generate_image(api_key,project['project_id'],kind,scene,False)
                        st.success('캐시 이미지 사용' if cached else '이미지 생성 완료'); st.image(path,use_container_width=True)
                    except Exception as e: st.error(str(e))
            with c2:
                if st.button(f'다시 생성 #{no}',key=f'regen_{kind}_{no}',disabled=not bool(api_key)):
                    try:
                        path,_=generate_image(api_key,project['project_id'],kind,scene,True)
                        st.success('새 이미지 생성 완료'); st.image(path,use_container_width=True)
                    except Exception as e: st.error(str(e))


def format_srt_time(seconds):
    ms=max(0,int(round(seconds*1000)))
    h,rem=divmod(ms,3600000); m,rem=divmod(rem,60000); s,ms=divmod(rem,1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def audio_duration(path):
    if path.lower().endswith('.wav'):
        with contextlib.closing(wave.open(path,'rb')) as w:
            return w.getnframes()/float(w.getframerate())
    return None

def split_caption_units(script):
    text=re.sub(r'\s+',' ',script or '').strip()
    if not text: return []
    units=[x.strip() for x in re.split(r'(?<=[.!?。！？])\s+|(?<=다[.!?])|(?<=요[.!?])',text) if x.strip()]
    if len(units)<=1:
        units=[x.strip() for x in re.split(r'(?<=[,.])\s*',text) if x.strip()]
    return units or [text]

def build_srt(script, duration):
    units=split_caption_units(script)
    if not units: return ''
    weights=[max(1,len(re.sub(r'\s','',u))) for u in units]; total=sum(weights); t=0.0; out=[]
    for i,(u,w) in enumerate(zip(units,weights),1):
        end=duration if i==len(units) else t+duration*w/total
        out += [str(i), f'{format_srt_time(t)} --> {format_srt_time(end)}', u, '']
        t=end
    return '\n'.join(out)

def make_tts(api_key, project_id, kind, script, voice='alloy'):
    from openai import OpenAI
    folder=os.path.join(PROJECTS_DIR,project_id,'audio',kind); os.makedirs(folder,exist_ok=True)
    wav_path=os.path.join(folder,'narration.wav')
    client=OpenAI(api_key=api_key)
    with client.audio.speech.with_streaming_response.create(model=st.session_state.get('tts_model_runtime','gpt-4o-mini-tts'),voice=voice,input=script,response_format='wav') as response:
        response.stream_to_file(wav_path)
    duration=audio_duration(wav_path)
    srt=build_srt(script,duration)
    srt_path=os.path.join(folder,'subtitles.srt')
    with open(srt_path,'w',encoding='utf-8') as f: f.write(srt)
    return wav_path,srt_path,duration

def render_audio_panel(project, kind, api_key, voice):
    content=project.get(kind,{})
    script=content.get('script','').strip()
    if not script: return
    st.subheader('음성 · 자막')
    folder=os.path.join(PROJECTS_DIR,project['project_id'],'audio',kind)
    wav_path=os.path.join(folder,'narration.wav'); srt_path=os.path.join(folder,'subtitles.srt')
    if os.path.exists(wav_path): st.audio(wav_path)
    if os.path.exists(srt_path):
        with open(srt_path,encoding='utf-8') as f: st.code(f.read(),language=None)
    if st.button(f'{kind.upper()} TTS + SRT 생성',key=f'tts_{kind}',disabled=not bool(api_key),use_container_width=True):
        try:
            wav,srt,duration=make_tts(api_key,project['project_id'],kind,script,voice)
            project['pipeline']['tts']='done'; project['pipeline']['subtitles']='done'
            project.setdefault('media',{}).setdefault(kind,{})
            project['media'][kind].update({'audio':wav,'subtitles':srt,'duration_seconds':round(duration,3)})
            save_project(project)
            st.success(f'음성·SRT 생성 완료 · {duration:.1f}초'); st.audio(wav)
            with open(srt,encoding='utf-8') as f: st.code(f.read(),language=None)
        except Exception as e: st.error(f'TTS 생성 오류: {e}')


def ffmpeg_exists():
    return shutil.which('ffmpeg') is not None

def _ffmpeg_path(path):
    return os.path.abspath(path).replace('\\','/')

def render_mp4(project, kind):
    if not ffmpeg_exists():
        raise RuntimeError('FFmpeg가 설치되어 있지 않습니다. 서버에 FFmpeg를 설치해 주세요.')
    content=project.get(kind,{})
    scenes=content.get('scenes',[])
    if not scenes: raise RuntimeError('장면 정보가 없습니다.')
    audio=project.get('media',{}).get(kind,{}).get('audio') or os.path.join(PROJECTS_DIR,project['project_id'],'audio',kind,'narration.wav')
    srt=project.get('media',{}).get(kind,{}).get('subtitles') or os.path.join(PROJECTS_DIR,project['project_id'],'audio',kind,'subtitles.srt')
    if not os.path.exists(audio): raise RuntimeError('먼저 TTS 음성을 생성해 주세요.')
    duration=audio_duration(audio)
    if not duration: raise RuntimeError('음성 길이를 확인할 수 없습니다.')
    img_dir=os.path.join(PROJECTS_DIR,project['project_id'],'images',kind)
    image_paths=[]
    missing=[]
    for scene in scenes:
        no=int(scene.get('scene',len(image_paths)+1))
        candidates=[]
        if os.path.isdir(img_dir):
            candidates=[os.path.join(img_dir,f) for f in os.listdir(img_dir) if f.startswith(f'scene_{no:03d}_') and f.lower().endswith(('.png','.jpg','.jpeg','.webp'))]
        if not candidates: missing.append(no)
        else: image_paths.append(sorted(candidates,key=os.path.getmtime)[-1])
    if missing: raise RuntimeError('이미지를 먼저 생성해 주세요. 누락 장면: '+', '.join(map(str,missing)))
    out_dir=os.path.join(PROJECTS_DIR,project['project_id'],'render',kind); os.makedirs(out_dir,exist_ok=True)
    clips=[]; per=duration/max(1,len(image_paths)); fps=30
    W,H=(1080,1920) if kind=='shorts' else (1920,1080)
    for i,img in enumerate(image_paths,1):
        clip=os.path.join(out_dir,f'clip_{i:03d}.mp4')
        frames=max(1,int(round(per*fps)))
        vf=(f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"zoompan=z='min(zoom+0.0005,1.06)':d={frames}:s={W}x{H}:fps={fps},format=yuv420p")
        cmd=['ffmpeg','-y','-loop','1','-i',img,'-vf',vf,'-t',f'{per:.3f}','-r',str(fps),'-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',clip]
        r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        if r.returncode: raise RuntimeError('장면 렌더링 실패: '+r.stderr[-1200:])
        clips.append(clip)
    concat=os.path.join(out_dir,'concat.txt')
    with open(concat,'w',encoding='utf-8') as f:
        for c in clips: f.write("file '"+_ffmpeg_path(c).replace("'","'\\''")+"'\n")
    silent=os.path.join(out_dir,'silent.mp4')
    r=subprocess.run(['ffmpeg','-y','-f','concat','-safe','0','-i',concat,'-c','copy',silent],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if r.returncode: raise RuntimeError('장면 결합 실패: '+r.stderr[-1200:])
    final=os.path.join(out_dir,f'{kind}.mp4')
    cmd=['ffmpeg','-y','-i',silent,'-i',audio]
    if os.path.exists(srt):
        # subtitles filter needs escaped absolute path
        sp=_ffmpeg_path(srt).replace(':','\\:').replace("'","\\'")
        cmd += ['-vf',f"subtitles='{sp}':force_style='FontSize=18,Outline=2,Alignment=2,MarginV=80'"]
    cmd += ['-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac','-b:a','192k','-shortest','-movflags','+faststart',final]
    r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if r.returncode: raise RuntimeError('최종 MP4 렌더링 실패: '+r.stderr[-1800:])
    project.setdefault('media',{}).setdefault(kind,{})['mp4']=final
    project['pipeline']['render']='done'; save_project(project)
    return final

def render_video_panel(project, kind):
    st.subheader('최종 MP4')
    existing=project.get('media',{}).get(kind,{}).get('mp4') or os.path.join(PROJECTS_DIR,project['project_id'],'render',kind,f'{kind}.mp4')
    if os.path.exists(existing):
        st.video(existing)
        with open(existing,'rb') as f: st.download_button(f'{kind.upper()} MP4 다운로드',f,file_name=f'{project["project_id"]}_{kind}.mp4',mime='video/mp4',key=f'dl_{kind}',use_container_width=True)
    if st.button(f'🎬 {kind.upper()} 최종 MP4 렌더링',key=f'render_{kind}',use_container_width=True):
        try:
            with st.spinner('FFmpeg로 최종 영상을 렌더링하고 있습니다...'):
                final=render_mp4(project,kind)
            st.success('최종 MP4 완성')
            st.video(final)
            with open(final,'rb') as f: st.download_button(f'{kind.upper()} MP4 다운로드',f,file_name=f'{project["project_id"]}_{kind}.mp4',mime='video/mp4',key=f'dl_new_{kind}',use_container_width=True)
        except Exception as e: st.error(str(e))

def generate_all_images(api_key, project, kind):
    scenes=project.get(kind,{}).get('scenes',[])
    if not scenes: raise RuntimeError('장면 정보가 없습니다.')
    made=[]
    for scene in scenes:
        path,cached=generate_image(api_key,project['project_id'],kind,scene,False)
        made.append({'scene':scene.get('scene'), 'path':path, 'cached':cached})
    project['pipeline']['images']='done'; save_project(project)
    return made

def run_oneclick(project, kind, api_key, voice):
    if not api_key: raise RuntimeError('OpenAI API Key가 필요합니다.')
    generate_all_images(api_key,project,kind)
    script=project.get(kind,{}).get('script','').strip()
    if not script: raise RuntimeError('대본이 없습니다.')
    wav,srt,duration=make_tts(api_key,project['project_id'],kind,script,voice)
    project['pipeline']['tts']='done'; project['pipeline']['subtitles']='done'
    project.setdefault('media',{}).setdefault(kind,{})
    project['media'][kind].update({'audio':wav,'subtitles':srt,'duration_seconds':round(duration,3)})
    save_project(project)
    final=render_mp4(project,kind)
    return final

def run_full_pipeline(project, api_key, voice):
    """선택한 제작 유형 전체를 이미지→TTS/SRT→MP4까지 연속 처리."""
    kinds=[]
    if project.get('output') in ('쇼츠','둘 다') and 'shorts' in project: kinds.append('shorts')
    if project.get('output') in ('롱폼','둘 다') and 'longform' in project: kinds.append('longform')
    results={}
    for kind in kinds:
        results[kind]=run_oneclick(project,kind,api_key,voice)
    return results

def render_oneclick_panel(project, kind, api_key, voice):
    st.subheader('⚡ 원클릭 자동 제작')
    st.caption('장면 이미지 전체 생성 → TTS → SRT → FFmpeg MP4를 순서대로 실행합니다. 이미 생성된 이미지는 캐시를 재사용합니다.')
    if st.button(f'🚀 {kind.upper()} 원클릭 MP4 만들기', key=f'oneclick_{kind}', type='primary', use_container_width=True, disabled=not bool(api_key)):
        try:
            with st.status(f'{kind.upper()} 자동 제작 중...', expanded=True) as status:
                st.write('① 장면 이미지 전체 생성/캐시 확인')
                generate_all_images(api_key,project,kind)
                st.write('② TTS 음성 + SRT 자막 생성')
                script=project.get(kind,{}).get('script','').strip()
                wav,srt,duration=make_tts(api_key,project['project_id'],kind,script,voice)
                project['pipeline']['tts']='done'; project['pipeline']['subtitles']='done'
                project.setdefault('media',{}).setdefault(kind,{})
                project['media'][kind].update({'audio':wav,'subtitles':srt,'duration_seconds':round(duration,3)})
                save_project(project)
                st.write('③ FFmpeg 최종 렌더링')
                final=render_mp4(project,kind)
                status.update(label='MP4 완성',state='complete')
            st.video(final)
            with open(final,'rb') as f:
                st.download_button(f'{kind.upper()} MP4 다운로드',f,file_name=f'{project["project_id"]}_{kind}.mp4',mime='video/mp4',key=f'oneclick_dl_{kind}',use_container_width=True)
        except Exception as e:
            st.error(f'자동 제작 중단: {e}')

def list_projects():
    os.makedirs(PROJECTS_DIR,exist_ok=True)
    items=[]
    for path in glob.glob(os.path.join(PROJECTS_DIR,'*.json')):
        try:
            with open(path,'r',encoding='utf-8') as f: p=json.load(f)
            items.append({'path':path,'id':p.get('project_id',os.path.basename(path)[:-5]),'created_at':p.get('created_at',''),'topic':p.get('topic') or p.get('source',''),'output':p.get('output','')})
        except Exception: pass
    return sorted(items,key=lambda x:x.get('created_at',''),reverse=True)

def load_project(path):
    with open(path,'r',encoding='utf-8') as f: return json.load(f)

def pipeline_progress(project):
    steps=['input','reference_analysis','fact_check','script','image_prompts','images','tts','subtitles','render']
    done=sum(1 for k in steps if project.get('pipeline',{}).get(k)=='done')
    return int(done/len(steps)*100), done, len(steps)

def save_project(project):
    os.makedirs(PROJECTS_DIR,exist_ok=True)
    path=os.path.join(PROJECTS_DIR,project['project_id']+'.json')
    with open(path,'w',encoding='utf-8') as f: json.dump(project,f,ensure_ascii=False,indent=2)
    return path

st.title('🐕 Jindo Studio')
st.caption('진돗개 AI 정보영상 자동 제작기 · v1.0 · 모바일 원클릭 배포판')

st.info('📱 휴대폰에서는 소재/링크 입력 → 제작 유형 선택 → 자동 제작 시작만 하면 됩니다. 처음 테스트는 쇼츠 1편을 권장합니다.')

with st.expander('📂 이전 프로젝트 불러오기', expanded=False):
    plist=list_projects()
    if plist:
        labels=[f"{x['created_at'][:16]} · {x['output']} · {str(x['topic'])[:55]} · {x['id']}" for x in plist]
        idx=st.selectbox('저장된 프로젝트',range(len(plist)),format_func=lambda i: labels[i])
        if st.button('선택 프로젝트 불러오기',use_container_width=True):
            st.session_state['loaded_project']=load_project(plist[idx]['path'])
            st.success('프로젝트를 불러왔습니다. 아래에서 이어서 작업할 수 있습니다.')
    else:
        st.info('아직 저장된 프로젝트가 없습니다.')

loaded=st.session_state.get('loaded_project')
if loaded:
    pct,done,total=pipeline_progress(loaded)
    st.progress(pct,text=f"불러온 프로젝트 진행률 {pct}% · {done}/{total} 단계 완료")
    st.write(f"**프로젝트:** {loaded.get('project_id')}  |  **제작:** {loaded.get('output','')}  |  **입력:** {loaded.get('source','')}")
    if loaded.get('auto_pipeline_error') or loaded.get('ai_error'):
        st.warning('이전 작업에 오류 기록이 있습니다. 완료된 이미지/음성은 재사용하고 실패 지점부터 다시 실행할 수 있습니다.')
    c1,c2,c3=st.columns(3)
    if c1.button('▶ 실패 지점부터 자동 재시작',use_container_width=True):
        api=st.session_state.get('api_key_runtime','')
        voice_rt=st.session_state.get('voice_runtime','alloy')
        if not api: st.error('먼저 사이드바에 OpenAI API Key를 입력하세요.')
        else:
            try:
                with st.status('저장된 결과를 재사용하며 이어서 제작 중...',expanded=True) as rs:
                    kinds=[]
                    if loaded.get('output') in ('쇼츠','둘 다') and 'shorts' in loaded: kinds.append('shorts')
                    if loaded.get('output') in ('롱폼','둘 다') and 'longform' in loaded: kinds.append('longform')
                    for kind in kinds:
                        generate_all_images(api,loaded,kind)
                        script=loaded.get(kind,{}).get('script','').strip()
                        wav,srt,duration=make_tts(api,loaded['project_id'],kind,script,voice_rt)
                        loaded.setdefault('media',{}).setdefault(kind,{}).update({'audio':wav,'subtitles':srt,'duration_seconds':round(duration,3)})
                        loaded['pipeline']['tts']='done'; loaded['pipeline']['subtitles']='done'
                        save_project(loaded); render_mp4(loaded,kind)
                    rs.update(label='재시작 작업 완료',state='complete')
                st.session_state['loaded_project']=loaded
            except Exception as e: st.error(f'재시작 중단: {e}')
    if c2.button('🗑 불러온 프로젝트 닫기',use_container_width=True):
        st.session_state.pop('loaded_project',None); st.rerun()
    if c3.button('🔄 화면 새로고침',use_container_width=True): st.rerun()
    for kind in ('shorts','longform'):
        mp4=loaded.get('media',{}).get(kind,{}).get('mp4')
        if mp4 and os.path.exists(mp4):
            st.video(mp4)
            with open(mp4,'rb') as f: st.download_button(f'{kind.upper()} MP4 다운로드',f,file_name=f"{loaded['project_id']}_{kind}.mp4",mime='video/mp4',key=f'loaded_dl_{kind}',use_container_width=True)
    st.divider()

with st.sidebar:
    st.header('AI 설정')
    env_key=os.getenv('OPENAI_API_KEY','')
    api_key=st.text_input('OpenAI API Key', value=env_key, type='password', help='키는 프로젝트 JSON에 저장하지 않습니다.')
    model=st.text_input('텍스트 모델 ID', value=os.getenv('JINDO_TEXT_MODEL','gpt-5.6'), help='계정에서 사용 가능한 OpenAI API 모델 ID를 입력하세요.')
    image_model=st.text_input('이미지 모델 ID', value=os.getenv('JINDO_IMAGE_MODEL','gpt-image-2'))
    tts_model=st.text_input('TTS 모델 ID', value=os.getenv('JINDO_TTS_MODEL','gpt-4o-mini-tts'))
    st.caption('모델 ID는 코드에 고정하지 않고 설정에서 바꿀 수 있습니다.')
    voice=st.selectbox('TTS 음성',['alloy','ash','ballad','coral','echo','fable','nova','onyx','sage','shimmer'],index=0)
st.session_state['api_key_runtime']=api_key
st.session_state['voice_runtime']=voice
st.session_state['image_model_runtime']=image_model
st.session_state['tts_model_runtime']=tts_model

preset_name=st.segmented_control('콘텐츠 프리셋',['한국용','일본용'],default='한국용') if hasattr(st,'segmented_control') else st.radio('콘텐츠 프리셋',['한국용','일본용'],horizontal=True)
rules=active_rules(preset_name)

source=st.text_area('소재 또는 YouTube 레퍼런스 링크', placeholder='예: 진돗개가 가족의 빈자리에 가서 앉는 이유\n또는 https://youtu.be/...', height=100)
ref_text=st.text_area('레퍼런스 자막/요약 (선택)', placeholder='영상 내용을 더 정확히 분석하려면 합법적으로 확보한 자막이나 직접 요약한 내용을 붙여 넣으세요.', height=120)
col1,col2=st.columns(2)
with col1: output=st.radio('제작 유형',['쇼츠','롱폼','둘 다'],index=2,horizontal=True)
with col2: mode=st.radio('제작 방식',['완전자동','단계별 확인'],index=0,horizontal=True)
with st.expander('진돗개 기본 프리셋'): st.json(rules)

pre1,pre2,pre3=st.columns(3)
pre1.metric('OpenAI API','준비됨' if api_key else '키 필요')
pre2.metric('FFmpeg','준비됨' if ffmpeg_exists() else '설치 필요')
pre3.metric('제작 모드',mode)

if st.button('🚀 AI 콘텐츠 제작 시작', type='primary', use_container_width=True):
    if not source.strip(): st.error('소재 또는 YouTube 링크를 입력해 주세요.')
    else:
        vid=youtube_id(source); meta=fetch_oembed(source.strip()) if vid else {'status':'not_needed'}
        ref=reference_plan(source,vid,meta,ref_text)
        ref['preset_name']=preset_name; ref['preset_rules']=rules
        topic=ref['topic']
        pid=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
        project={'schema_version':'1.0','project_id':pid,'created_at':datetime.datetime.now().isoformat(),
                 'source':source.strip(),'source_type':'youtube' if vid else 'topic','youtube_id':vid,'youtube_metadata':meta,
                 'reference_analysis':ref,'output':output,'mode':mode,'preset_name':preset_name,'preset':rules,'model':model,'image_model':image_model,'tts_model':tts_model,
                 'pipeline':{'input':'done','reference_analysis':'done','fact_check':'pending','script':'pending','image_prompts':'pending','images':'pending','tts':'pending','subtitles':'pending','render':'pending'}}
        with st.status('Jindo Studio가 콘텐츠를 제작하고 있습니다...', expanded=True) as status:
            st.write('① 입력 및 레퍼런스 분석 완료')
            if api_key:
                try:
                    result, raw=ai_generate(api_key,model,topic,output,ref,ref_text)
                    project['reference_insights']=result.get('reference_insights',{})
                    project['fact_check']=result.get('fact_check',{})
                    if 'shorts' in result: project['shorts']=result['shorts']
                    if 'longform' in result: project['longform']=result['longform']
                    project['pipeline'].update({'fact_check':'done','script':'done','image_prompts':'done'})
                    st.write('② 웹 검색 기반 사실검증 완료')
                    st.write('③ 쇼츠/롱폼 신규 대본 생성 완료')
                    st.write('④ 장면 분할 + 이미지 프롬프트 생성 완료')
                    status.update(label='콘텐츠/프롬프트 생성 완료',state='complete')
                except Exception as e:
                    project['ai_error']=str(e)
                    if output in ('쇼츠','둘 다'): project['shorts']=fallback_content(topic,'shorts')
                    if output in ('롱폼','둘 다'): project['longform']=fallback_content(topic,'longform')
                    status.update(label='AI 호출 실패 — fallback 프로젝트를 생성했습니다',state='error')
                    st.error(f'AI 연결 오류: {e}')
            else:
                if output in ('쇼츠','둘 다'): project['shorts']=fallback_content(topic,'shorts')
                if output in ('롱폼','둘 다'): project['longform']=fallback_content(topic,'longform')
                status.update(label='API Key가 없어 설계 초안만 생성했습니다',state='complete')
                st.warning('사이드바에 OpenAI API Key를 입력하면 사실검증과 실제 대본 생성이 작동합니다.')
        path=save_project(project)
        est=estimate_work(project)
        st.caption(f"예상 작업량 · 이미지 최대 {est['image_calls_max']}회 · TTS {est['tts_calls']}회 · MP4 {est['render_outputs']}개. {est['note']}")
        auto_results={}
        if mode=='완전자동' and api_key and project.get('pipeline',{}).get('script')=='done':
            try:
                with st.status('🚀 완전자동 모드: 최종 MP4까지 제작 중...', expanded=True) as auto_status:
                    kinds=[]
                    if output in ('쇼츠','둘 다') and 'shorts' in project: kinds.append('shorts')
                    if output in ('롱폼','둘 다') and 'longform' in project: kinds.append('longform')
                    for kind in kinds:
                        st.write(f'{kind.upper()} · 이미지 전체 생성/캐시 확인')
                        generate_all_images(api_key,project,kind)
                        st.write(f'{kind.upper()} · TTS + SRT 생성')
                        script=project.get(kind,{}).get('script','').strip()
                        wav,srt,duration=make_tts(api_key,project['project_id'],kind,script,voice)
                        project['pipeline']['tts']='done'; project['pipeline']['subtitles']='done'
                        project.setdefault('media',{}).setdefault(kind,{})
                        project['media'][kind].update({'audio':wav,'subtitles':srt,'duration_seconds':round(duration,3)})
                        save_project(project)
                        st.write(f'{kind.upper()} · FFmpeg 최종 렌더링')
                        auto_results[kind]=render_mp4(project,kind)
                    auto_status.update(label='쇼츠/롱폼 자동 제작 완료',state='complete')
            except Exception as e:
                project['auto_pipeline_error']=str(e)
                save_project(project)
                st.error(f'완전자동 제작 중단: {e}')
        progress_value=100 if auto_results else 65
        progress_text='최종 MP4 ✓' if auto_results else '입력 ✓  레퍼런스 ✓  대본/프롬프트 ✓  → 이미지 → TTS/SRT → MP4'
        st.progress(progress_value,text=progress_text)
        if auto_results:
            st.success('🎬 완전자동 제작이 끝났습니다.')
            for kind, final in auto_results.items():
                st.subheader(f'{kind.upper()} 최종 MP4')
                st.video(final)
                with open(final,'rb') as f:
                    st.download_button(f'{kind.upper()} MP4 다운로드',f,file_name=f'{project["project_id"]}_{kind}.mp4',mime='video/mp4',key=f'auto_top_dl_{kind}',use_container_width=True)
        tabs=st.tabs(['검증/분석','SHORTS','LONGFORM','프로젝트'])
        with tabs[0]:
            st.subheader('레퍼런스 분석'); st.json(project.get('reference_insights',ref))
            st.subheader('사실 검증'); st.json(project.get('fact_check',{'status':'AI 연결 필요'}))
        with tabs[1]:
            if 'shorts' in project:
                s=project['shorts']; st.subheader(s.get('title','SHORTS')); st.write('**썸네일**'); st.code(s.get('thumbnail',''))
                st.write('**대본**'); st.write(s.get('script','')); st.write('**장면**'); st.json(s.get('scenes',[])); render_oneclick_panel(project,'shorts',api_key,voice); render_image_panel(project,'shorts',api_key); render_audio_panel(project,'shorts',api_key,voice); render_video_panel(project,'shorts')
            else: st.info('쇼츠를 선택하지 않았습니다.')
        with tabs[2]:
            if 'longform' in project:
                l=project['longform']; st.subheader(l.get('title','LONGFORM')); st.write('**썸네일**'); st.code(l.get('thumbnail',''))
                st.write('**대본**'); st.write(l.get('script','')); st.write('**챕터**'); st.json(l.get('chapters',[])); st.write('**장면**'); st.json(l.get('scenes',[])); render_oneclick_panel(project,'longform',api_key,voice); render_image_panel(project,'longform',api_key); render_audio_panel(project,'longform',api_key,voice); render_video_panel(project,'longform')
            else: st.info('롱폼을 선택하지 않았습니다.')
        with tabs[3]:
            st.json(project['pipeline'])
            with open(path,'rb') as f: st.download_button('프로젝트 JSON 다운로드',f,file_name=os.path.basename(path),mime='application/json',use_container_width=True)
        st.success('v0.9: 프로젝트 저장/불러오기와 실패 지점 재시작을 지원합니다. 완료된 생성물은 캐시를 재사용합니다.')
