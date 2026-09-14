const EDGE_URL='https://ijkdhrznxukawugeoocs.supabase.co/functions/v1/twinerun-waitlist';

export default async function handler(req,res){
  if(req.method!=='POST') return res.status(405).json({error:'Method not allowed'});

  const email=String(req.body?.email||'').trim().toLowerCase();
  if(!/^\S+@\S+\.\S+$/.test(email)) return res.status(400).json({error:'Enter a valid email.'});

  const payload={
    email,
    source:String(req.body?.source||'landing').slice(0,80),
    ua:String(req.body?.ua||'').slice(0,500),
    website:String(req.body?.website||'').slice(0,200)
  };

  try{
    const upstream=await fetch(EDGE_URL,{
      method:'POST',
      headers:{'content-type':'application/json','accept':'application/json'},
      body:JSON.stringify(payload)
    });

    const body=await upstream.text();
    let parsed={};
    try{parsed=JSON.parse(body)}catch{}

    if(!upstream.ok || parsed?.ok!==true){
      console.error('waitlist_supabase_failed',{status:upstream.status,body:body.slice(0,600)});
      throw new Error(parsed?.error||`Supabase edge ${upstream.status}`);
    }

    return res.status(200).json({ok:true});
  }catch(err){
    console.error('waitlist_save_failed',err);
    return res.status(502).json({error:'Could not save your email. Try again shortly.'});
  }
}
