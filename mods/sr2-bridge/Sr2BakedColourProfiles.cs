using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Linear material gains measured from ungraded SR2 1.3 HDRP captures against the
    /// authored reference renders. Only these exact baked cache revisions are calibrated.
    /// Texture changes, unknown assets and fallback shaders stay neutral; PNGs stay untouched.
    /// </summary>
    internal static class Sr2BakedColourProfiles
    {
        private readonly struct Profile
        {
            internal readonly string Model, Manifest;
            internal readonly Vector4 Gain;
            internal Profile(string model, string manifest, Vector4 gain)
            { Model = model; Manifest = manifest; Gain = gain; }
        }

        private static readonly Dictionary<string, Profile> Profiles = new Dictionary<string, Profile>(StringComparer.Ordinal)
        {
            {"largo.angler.mosaic", new Profile("893e4593906d6cfb786818451ba1f8e927a7e111ef3fd5c73666dd639679598e", "fb0e19fcb3c159e4dcb666e67bbbe4ad775a4f86190e9bc6b5955b042e16a06d", new Vector4(0.569186102f, 0.578918605f, 0.60060611f, 1f))},
            {"largo.angler.quantum", new Profile("c1048a2367cd9633520ff4148de9a71dd3c87a4abe5e6e0b1c72d4adbf518ff0", "4d1bc7828e01328d2a5a3480c250498b2895a0441b4b10ccc62939b0dbae4897", new Vector4(0.454392281f, 0.540141447f, 0.499692805f, 1f))},
            {"largo.angler.rad", new Profile("d248d680eeb227e21b3ab58a4d8bcc6428f31826b2ec68e1b7324e2b8cb6b9e8", "f3f0d88e7d5068416acf8a460a6a48ac91b2c5766161e9bd9be78017644482bf", new Vector4(0.394429724f, 0.573796643f, 0.526100216f, 1f))},
            {"largo.batty.mosaic", new Profile("e052f50cf35e381113fb7e66990e11247429b66cade3ba544b21442852f861b3", "1636bce99c5c77639c39262982ecf260cf9823c3154c80c5fc86a34997481be4", new Vector4(0.545963091f, 0.480096375f, 0.562862232f, 1f))},
            {"largo.batty.quantum", new Profile("0cca1b0397174996eb35c41f0f3209d736f54a99b747b74c0864af87e67a72cb", "4d916ed4c50dfc56dda335c377f0d77b7b1e4f8d21b078a423c1e855353b3a87", new Vector4(0.378117154f, 0.361863472f, 0.458471113f, 1f))},
            {"largo.batty.rad", new Profile("e4b33b5d91c70e64a6bee5ec23fd8924aaad147d4ba2f9bf78f6ed403a30bb17", "f643d482f952c6af547b98d21f7c1713ed0d144a49eeebafd0919fa5e140bda9", new Vector4(0.426286433f, 0.62738886f, 0.536849447f, 1f))},
            {"largo.cotton.mosaic", new Profile("ff2976c34c1fa2d9ed1008b8931c35297eeada5424fbc8ca06840af3a5f90d15", "b8d53b72c735f0822d17419db477d13a1398841751ad19aaab7951ca8605899d", new Vector4(0.675085404f, 0.534743564f, 0.52379735f, 1f))},
            {"largo.cotton.quantum", new Profile("852bbc74ea2b19ab28547ecb3f0e8bb979a99c3c6fa9e8b45b957e9631271068", "09e689d8f779a6724ca0e741878330567441a80cd8c9fa3e9728523e73b2ba5d", new Vector4(0.621367578f, 0.478820899f, 0.366666617f, 1f))},
            {"largo.cotton.rad", new Profile("83ff3e4155ceb03acc216a58b95b4f2f30c24919413b646d57d511a75a150ffc", "eb6c4ae429204daf0185fe611f9ae3bd0babd5d8652b1c639fa1545eedc97368", new Vector4(0.571326778f, 0.676074993f, 0.508213397f, 1f))},
            {"largo.flutter.mosaic", new Profile("0d3d1b692c74578410bb9cb3f2484d5ad686037bc6900a7bfe9bf992338a842b", "1ccd2b6be7d1b0844a21e72c3d5d9ab5a79285383bd3821fd2995bdc43ea4a3f", new Vector4(0.631824093f, 0.520607237f, 0.547654114f, 1f))},
            {"largo.flutter.quantum", new Profile("22bc315bf673847f96cf48ad196c1281c008aef784b22b042a86bf2a33269d0d", "7e1ca4c1a32a1fc8c8baab8c98671e6184584866a9d2376111d6584f79b62fe3", new Vector4(0.53890038f, 0.409593505f, 0.414013812f, 1f))},
            {"largo.flutter.rad", new Profile("cecc51eb5563e449db753b97ad46992f41289fe8f77075a3b5dc8df99d869c97", "c7fdbe7a336e9228b742671eb1d0696999d4e92cd70b3af8814c53de9833519f", new Vector4(0.636067168f, 0.554427469f, 0.406984127f, 1f))},
            {"largo.hyper.mosaic", new Profile("deb6459553c40675f7c7ba203ecc34eca125b88d5ee391d72957b421fad5eb3e", "179585865f0828e901ed9427436001988756ba2363d616d7a570da1cfc42a653", new Vector4(0.697329116f, 0.479815866f, 0.537991022f, 1f))},
            {"largo.hyper.quantum", new Profile("8ec2527c8147f703652244893387a2686ba3f188a07549064981cce58bfd087c", "1c89e2984a28b96c14f9e1cf1b2252e64b414c155c53830b8e726f96521f8ce9", new Vector4(0.739432958f, 0.727639568f, 0.77824258f, 1f))},
            {"largo.hyper.rad", new Profile("ba163ed60c438f0b809686e4c3365916ecfa40600636ce14a60e62b0d36f8f7f", "a8a835637abbb2ccc44f807ca29d69260e03e0dfb651f175c8df48c37f9fba92", new Vector4(0.538824632f, 0.574155864f, 0.358914775f, 1f))},
            {"largo.mosaic.ringtail", new Profile("45e34e98bc659e88eaaf52aa8fdaeb82880295c62a611d0d84903a8859e0b909", "a752b49369c76c907ca14673b8e4d4fd3393efb398cc62c321dd70a30753d0ad", new Vector4(0.676739324f, 0.558701386f, 0.573047784f, 1f))},
            {"largo.mosaic.sloomber", new Profile("b6a06eac98a2fa025424f4cf5b0766479bf9c49356cbc79e9dde0a5fb1dcacc0", "fce9af0db98cf8f16afb9309421bec7490fe64876356e278da86614c5d0c85ea", new Vector4(0.670452398f, 0.579014002f, 0.71007852f, 1f))},
            {"largo.mosaic.twin", new Profile("ed0dafcb2a5e874e29253f98e8c6490e7067cd59b9b652579525945230f90f63", "bf6e9e07e28f446538d49031204fa37613945bb51577da9f4b98ee2901d75930", new Vector4(0.569511907f, 0.497229039f, 0.585358684f, 1f))},
            {"largo.quantum.ringtail", new Profile("f02cbca77b897dd90efcd347f87a192c6335d34408df3cc4f8ca0463c0a10ace", "50a7149d8a7f9124a78b6262f22a67693d42d0ca3d7c9b0d33edef0b9f305dba", new Vector4(0.616224561f, 0.55850025f, 0.607751423f, 1f))},
            {"largo.quantum.sloomber", new Profile("2c7afcf4d16ed20bb02c6ef2b16965f63c044cc2f6d8c6065cb9172b02e2cdec", "5574d59d62bdb2abda11e024b74c15edeb645a2cc8ae469dd18ed90ff3227bb8", new Vector4(0.549616604f, 0.470227164f, 0.50968616f, 1f))},
            {"largo.quantum.twin", new Profile("ecaa6dbb87ad722dde675f135188f843e8f3e1e476e3ee6f0a07434d0c1b3852", "f3e0c33b2609d79a0a2717ceef23ffbbc228b557509197e1d0db4b8651c150ee", new Vector4(0.587303005f, 0.445115665f, 0.332780139f, 1f))},
            {"largo.rad.ringtail", new Profile("bb897da26d87f609e217dea71fe9f915464943be7db947786584d148b900fbaf", "4a3bec6c577a6e941ff8cabb97fe72ac25c1ed555ca8eae5079f5ee08fa00cc1", new Vector4(0.444285238f, 0.533192402f, 0.343617003f, 1f))},
            {"largo.rad.sloomber", new Profile("4246e3db0411fee72efcf8ebc2be7c3a6c2ecc42e071e8a3cce65622059f8b3a", "9aff0c92e75001618485076e7aa96036fe318665a5da40c21c730fc514f4d432", new Vector4(0.416558382f, 0.457477514f, 0.462800027f, 1f))},
            {"largo.rad.twin", new Profile("b2bbbb9f31a2bf11bc8734025ee034956a3444f1e65c713c0111701ef84e5dfb", "b45e78698f2861981c4990d9866172a192ce0a788fbef585222c4dba246c8fc2", new Vector4(0.350339607f, 0.550554156f, 0.567469005f, 1f))},
        };

        internal static Vector4 Resolve(string canonical, string model, string manifest, string shader)
        {
            if (shader == "HDRP/Lit" && canonical != null && Profiles.TryGetValue(canonical, out var profile)
                && string.Equals(model, profile.Model, StringComparison.Ordinal)
                && string.Equals(manifest, profile.Manifest, StringComparison.Ordinal)) return profile.Gain;
            return new Vector4(1f, 1f, 1f, 1f);
        }
    }
}
