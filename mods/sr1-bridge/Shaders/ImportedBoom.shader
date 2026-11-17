Shader "SlimeBridge/ImportedBoom"
{
    Properties
    {
        _TopColor ("Top", Color) = (1,.39,.39,1)
        _MiddleColor ("Middle", Color) = (.81,0,0,1)
        _BottomColor ("Bottom", Color) = (.42,0,0,1)
        _Gloss ("Gloss", Range(0,2)) = .7
        _GlossPower ("Gloss power", Range(0,1)) = .667
        _Cracks ("Native crack cubemap", Cube) = "black" {}
        _CrackAmount ("Native readiness", Range(0,1)) = .1
        _Char ("Native recovery", Range(0,1)) = 0
        _AttachmentCracks ("Ear UV projection", Range(0,1)) = 0
        [HDR] _CrackColor ("Cracks", Color) = (2,1,.25,1)
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" "Queue"="Geometry" }
        LOD 200
        Pass
        {
        Tags { "LightMode"="ForwardBase" }
        CGPROGRAM
        #pragma vertex vert
        #pragma fragment frag
        #pragma target 3.0
        #pragma multi_compile_fwdbase
        #pragma multi_compile_fog
        #include "UnityCG.cginc"
        #include "Lighting.cginc"
        samplerCUBE _Cracks;
        fixed4 _TopColor, _MiddleColor, _BottomColor;
        half4 _CrackColor;
        half _Gloss, _GlossPower, _CrackAmount, _Char, _AttachmentCracks;
        struct Input { float4 vertex : POSITION; float3 normal : NORMAL; float2 uv : TEXCOORD0; };
        struct Varyings { float4 position : SV_POSITION; float3 localNormal : TEXCOORD0; float3 worldNormal : TEXCOORD1; float3 worldPosition : TEXCOORD2; UNITY_FOG_COORDS(3) float2 uv : TEXCOORD4; };
        Varyings vert(Input input)
        {
            Varyings output;
            output.position = UnityObjectToClipPos(input.vertex);
            output.localNormal = input.normal;
            output.worldNormal = UnityObjectToWorldNormal(input.normal);
            output.worldPosition = mul(unity_ObjectToWorld,input.vertex).xyz;
            output.uv = input.uv;
            UNITY_TRANSFER_FOG(output,output.position);
            return output;
        }
        half4 frag(Varyings input) : SV_Target
        {
            float3 localNormal = normalize(input.localNormal);
            float3 worldNormal = normalize(input.worldNormal);
            half height = saturate(localNormal.y * .5 + .5);
            half3 body = height < .5 ? lerp(_BottomColor.rgb, _MiddleColor.rgb, height*2) : lerp(_MiddleColor.rgb, _TopColor.rgb, (height-.5)*2);
            half readiness = saturate(_CrackAmount);
            // A flat ear has almost one surface normal: project the same native line field
            // through its authored UVs instead, without changing its lighting or geometry.
            float longitude = input.uv.x * 6.2831853;
            float latitude = (input.uv.y-.5) * 3.1415927;
            float3 uvDirection = float3(cos(latitude)*sin(longitude),sin(latitude),cos(latitude)*cos(longitude));
            float3 crackDirection = normalize(lerp(localNormal,uvDirection,saturate(_AttachmentCracks)));
            half crackSample = texCUBElod(_Cracks, float4(crackDirection,0)).r;
            // A cubemap's line field supplies the emission. Do not add a view-facing core glow
            // to flat attachments: that was filling the rabbit ears instead of drawing fissures.
            half threshold = lerp(.35,.10,readiness);
            half fissure = smoothstep(threshold, threshold+.08, crackSample) * (1-saturate(_Char));
            float3 lightDirection = normalize(UnityWorldSpaceLightDir(input.worldPosition));
            float3 viewDirection = normalize(UnityWorldSpaceViewDir(input.worldPosition));
            half diffuse = saturate(dot(worldNormal,lightDirection));
            half specular = pow(saturate(dot(worldNormal,normalize(lightDirection+viewDirection))),exp2(5+5*saturate(_GlossPower))) * saturate(_Gloss);
            half3 lit = body * (.72+.28*diffuse) + specular * .5;
            half4 color = half4(lit * lerp(1,.07,saturate(_Char)) + fissure * _CrackColor.rgb * lerp(.65,1.5,readiness),1);
            UNITY_APPLY_FOG(input.fogCoord,color);
            return color;
        }
        ENDCG
        }
        UsePass "Legacy Shaders/VertexLit/SHADOWCASTER"
    }
    FallBack "Standard (Specular setup)"
}
