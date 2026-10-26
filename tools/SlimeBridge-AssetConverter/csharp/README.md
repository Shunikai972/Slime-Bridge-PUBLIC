# csharp/ — chargeur C# du paquet runtime

```
SbRuntime.Core/              netstandard2.0, sans Unity : Json.cs (strict), SbMesh.cs (.sbmesh), SbPackage.cs (modèle + validation + SHA-256)
SbRuntime.Unity/Shared/      SbBuilder.cs (Unity), Compat.cs (Mono vs IL2CPP : seul endroit qui diffère)
SbRuntime.Unity.SR1/         net472, compilé contre <SR1>\SlimeRancher_Data\Managed\UnityEngine.*.dll   (-p:SR1Managed=...)
SbRuntime.Unity.SR2/         net6.0, SB_IL2CPP, compilé contre <SR2>\MelonLoader\Il2CppAssemblies + net6\Il2CppInterop.*  (-p:SR2Root=...)
SbRuntime.Tests/             net8.0, lit des paquets réels ; tests/check_csharp_parity.py compare avec Python
UnityVerify/SbVerify.cs      script d'éditeur Unity (copié automatiquement par `sbconv unity-verify`)
```

Compiler (SDK .NET 8 ou plus récent) :

```powershell
dotnet build SbRuntime.Tests -c Release
dotnet build SbRuntime.Unity\SbRuntime.Unity.SR1 -c Release
dotnet build SbRuntime.Unity\SbRuntime.Unity.SR2 -c Release
python ..\tests\check_csharp_parity.py dotnet.exe SbRuntime.Tests\bin\Release\net8.0\SbRuntime.Tests.dll <dossier paquet runtime>...
```

Les DLL produites ne contiennent aucun asset ; elles se déposent à côté de ton mod (SRML : `SRML/Mods/<mod>/`, MelonLoader : `Mods/`
ou `UserLibs/`). Les références aux assemblies du jeu ne sont pas copiées (`Private=false`).
