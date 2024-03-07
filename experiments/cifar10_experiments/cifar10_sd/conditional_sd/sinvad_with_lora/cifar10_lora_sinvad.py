import os
from diffusers import StableDiffusionPipeline
from diffusers.schedulers import DDIMScheduler
import numpy as np
import torch
import wandb
from torch import autocast
from PIL import Image
from torchvision import transforms
from collections import Counter
from cifar10_classifier.model import VGGNet
run =wandb.init(project="sinvadtestfitness")
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":16:8"
torch.backends.cudnn.benchmark = False
generator = torch.Generator(device = 'cuda')
torch.use_deterministic_algorithms(True)
def calculate_fitness(logit, label):

    expected_logit = logit[label]
    # Select the two best indices [perturbed logit]
    best_indices = np.argsort(-logit)[:2]
    best_index1, best_index2 = best_indices
    if best_index1 == label:
        best_but_not_expected = best_index2
    else:
        best_but_not_expected = best_index1
    new_logit = logit[best_but_not_expected]
    # Compute fitness value
    fitness = expected_logit - new_logit
    return fitness
classifier = VGGNet().to(device)
# Load pretrained model
classifier.load_state_dict(
    torch.load(
        "./cifar10_classifier/CIFAR10_cifar10_train.pynet.pth",
        map_location=device,
   )
)
classifier.eval()
transform = transforms.Compose(
    [
        transforms.Resize((32, 32)),
        transforms.ToTensor(),
    ]
)
prompt = "A photo of truck9" # prompt to dream about
proj_name = "test"
num_inference_steps = 25
width = 512
height = 512
init_perturbation = 0.02
best_left = 10
perturbation_size = 0.01
frame_index = 0
gen_steps = 500
pop_size = 25
fitness_scores = []
all_img_lst = []
num_samples = 10
proj_path = "./evolution_cifar10_sdsinvadkpp/"+proj_name+"_"
os.makedirs(proj_path, exist_ok=True)
os.makedirs(proj_path+'/Newresult', exist_ok=True)


print('Creating init image')
#lms = EulerAncestralDiscreteScheduler(beta_start=0.0001, beta_end=0.02, beta_schedule="linear")
base_model_id = "runwayml/stable-diffusion-v1-5"
weights_path = "./cifar10_finetune_lorav1.5-000005.safetensors"

pipe = StableDiffusionPipeline.from_pretrained(
base_model_id, safety_checker=None).to(device)
pipe.load_lora_weights(weights_path)
pipe.scheduler = DDIMScheduler.from_config(pipe.scheduler.config)
pipe.unet.to(device)
pipe.vae.to(device)
pipe.text_encoder.to(device)
seed =1024
for n in range(num_samples):
    
    generator = generator.manual_seed(seed)
    original_lv = torch.randn((1, pipe.unet.config.in_channels, height // 8, width // 8),  device=device)
    with autocast("cuda"):
         init_img = pipe(prompt, num_inference_steps= num_inference_steps, latents=original_lv, width=width, height=height)["images"][0]
         init_img_path = os.path.join(proj_path, f'_origin_{n}.png')
         init_img.save(init_img_path)
         print(f"Original image {n} saved at {init_img_path}")
    tensor_image = transform(init_img)
    tensor_image = tensor_image.unsqueeze(0).to(device)
    original_logit = classifier(tensor_image).squeeze().detach().cpu().numpy()
    original_label = np.argmax(original_logit).item()
    init_pop = [
        original_lv + init_perturbation * torch.randn((4, height // 8, width // 8), device=device)
        for _ in range(pop_size)
    ]

    binom_sampler = torch.distributions.binomial.Binomial(
        probs=0.5 * torch.ones(original_lv.size(), device=device)
    )

    now_pop = init_pop
    prev_best = np.inf
    for i in range(gen_steps):

        indivs_lv = torch.cat(now_pop, dim=0).view(-1, 4, height // 8, width // 8)
        print(indivs_lv.shape)
        with torch.inference_mode(), torch.autocast("cuda"):
            perturb_img = pipe([prompt]*(pop_size),strength = 0.8,guidance_scale =1,
                num_inference_steps=num_inference_steps,generator = generator,
                latents=indivs_lv,
            )["images"]
        # all_img_lst.append(perturb_img)
        torch.cuda.empty_cache()
        
        tensor_image2 =torch.stack([transform(image) for image in perturb_img])
        tensor_image2 = tensor_image2.to(device)
        all_logits = classifier(tensor_image2).detach().cpu().numpy()
        perturb_label1 = np.argmax(all_logits).item()
        print(all_logits.shape)
        print(tensor_image2.shape)
        os.makedirs(os.path.join(proj_path, 'generated_images'), exist_ok=True)
        label_filenames = []

        # Loop through the logits and save each image with its label
       # for idx, logits in enumerate(all_logits):
        #   label = np.argmax(logits)
           # Get the image from perturb_img using idx
         #  image = perturb_img[idx]
          # perturb_label_filename = f'gen_{i}_image_{idx}_Y{label}.png'
    
           # Save the image with the label in the filename
          # image_filename = os.path.join(proj_path, 'generated_images', perturb_label_filename)
         #  image.save(image_filename)
    
           # Append the label and filename to the list
          # label_filenames.append((label, perturb_label_filename))

        # Save the label filenames to a text file
       # label_file = os.path.join(proj_path, 'generated_images', 'label_filenames.txt')
       # with open(label_file, 'w') as file:
        #    for label, filename in label_filenames:
         #      file.write(f"Label: {label}, Filename: {filename}\n")

        # Count the label occurrences and print them
      #  label_list = [label for label, _ in label_filenames]
       # label_counts = Counter(label_list)
       # for label, count in label_counts.items():
        #   print(f"Label {label}: Count {count}")
        fitness_scores = [
            calculate_fitness(all_logits[k_idx], original_label)
            for k_idx in range(pop_size)
        ]
        print("print fitness",len(fitness_scores))
        # Perform selection
        selected_indices = sorted(range(len(fitness_scores)), key=lambda i: fitness_scores[i], reverse=True,
           )[-best_left:]
        now_best = np.min(fitness_scores)
        parent_pop = [now_pop[idx] for idx in selected_indices]
        print("now_best " + str(now_best) + " average_best " + str(np.mean(fitness_scores)))
        wandb.log({"ft_score":now_best})
        if now_best < 0:
           break
        elif now_best == prev_best:
            perturbation_size *= 2
        else:
            perturbation_size = init_perturbation
        k_pop = []
        print("Size of parent_pop:", len(parent_pop))
        # select k-idx for cross_over genes
        for k_idx in range(pop_size - best_left):
            mom_idx, pop_idx = np.random.choice(best_left, size=2, replace=False)
            print("mom_idx:", mom_idx, "pop_idx:", pop_idx)
            spl_idx = np.random.choice(4, size=1)[0]
            k_gene = torch.cat(
                [parent_pop[mom_idx][:, :spl_idx], parent_pop[pop_idx][:, spl_idx:]],
                dim=1,
            )  # crossover

            # Mutation
            diffs = (k_gene != original_lv).float()
            k_gene += (
                   perturbation_size * torch.randn(k_gene.size(), device=k_gene.device) * diffs
            )  # random adding noise only to diff places
            interp_mask = binom_sampler.sample()
            k_gene = interp_mask * original_lv + (1 - interp_mask) * k_gene
            k_pop.append(k_gene)
            print("Size of k_pop:", len(k_pop))

        # Combine parent_pop and k_pop for the next generation
        now_pop = parent_pop + k_pop
        prev_best = now_best

    mod_best = parent_pop[-1].view(1, 4, height // 8, width // 8)
    
    with torch.inference_mode():

       last_image_list = pipe(prompt,guidance_scale=0, num_inference_steps= num_inference_steps, latents=mod_best)["images"]
    # After the loop, save the last image if it exists
    if last_image_list:
       last_image = last_image_list[0]
       tensor_image = transform(last_image)
       tensor_image = tensor_image.unsqueeze(0).to(device)
       perturb_logit = classifier(tensor_image).squeeze().detach().cpu().numpy()
       perturb_label = np.argmax(perturb_logit).item()

    if last_image is not None:
       image_filename = f'image_{n}_iteration{i}_X{original_label}_Y{perturb_label}.png'
       last_image_path = os.path.join(proj_path, 'Newresultjump', image_filename)
       last_image.save(last_image_path)
       print(f"Last image saved at {last_image_path}")
    else:
       print("Error: No image was generated")